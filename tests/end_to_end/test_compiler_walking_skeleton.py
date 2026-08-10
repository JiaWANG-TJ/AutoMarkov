from __future__ import annotations

import copy
import subprocess
import sys
from collections.abc import Callable
from typing import Any, cast

import pytest
from pydantic import TypeAdapter, ValidationError

from automarkov.adapters import InMemoryCompiler
from automarkov.api import compile_task
from automarkov.domain import (
    CompilerDispatchRequest,
    RequestBudget,
    RunId,
    RunState,
    RunView,
    TaskRequest,
    validate_task_request_payload,
)
from automarkov.errors import (
    CapabilityDeferredError,
    RunIdCollisionError,
    UnknownRunError,
)


def make_request(request_id: str = "request_walking_skeleton") -> TaskRequest:
    return validate_task_request_payload(
        {
            "schema_version": "automarkov.task-request.v1",
            "request_id": request_id,
            "task_text": (
                "Model a finite-horizon inventory replenishment decision process."
            ),
            "budget": {
                "schema_version": "automarkov.request-budget.v1",
                "wall_time_seconds": 60,
                "llm_token_limit": 0,
                "tool_call_limit": 0,
            },
            "permissions": {
                "schema_version": "automarkov.request-permissions.v1",
                "allow_retrieval": False,
                "allow_clarification": False,
                "allow_code_execution": False,
            },
        },
    )


def fixed_run_id_factory(value: str) -> Callable[[], RunId]:
    run_id = RunId(root=value)
    return lambda: run_id


def test_python_api_and_cli_return_the_same_canonical_received_view() -> None:
    request = make_request()
    expected_run_id = RunId(root="run_walking_skeleton")
    compiler = InMemoryCompiler(
        run_id_factory=fixed_run_id_factory(expected_run_id.root)
    )

    run_id = compiler.start(request)
    started_view = compiler.resume(run_id)
    resumed_view = compiler.resume(run_id)
    api_view = compile_task(
        request,
        compiler=InMemoryCompiler(
            run_id_factory=fixed_run_id_factory(expected_run_id.root)
        ),
    )

    assert run_id == expected_run_id
    assert isinstance(started_view, RunView)
    assert started_view == resumed_view == api_view
    assert started_view == RunView(
        schema_version="automarkov.run-view.v1",
        run_id=expected_run_id,
        task_request_id=request.request_id,
        state=RunState.RECEIVED,
    )
    with pytest.raises(ValidationError, match="frozen"):
        started_view.state = RunState.RESEARCHING

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "automarkov",
            "compile",
            "--request-id",
            request.request_id,
            "--task-text",
            request.task_text,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    cli_view = RunView.model_validate_json(completed.stdout)
    assert completed.stdout == f"{cli_view.model_dump_json()}\n"
    assert cli_view.schema_version == api_view.schema_version
    assert cli_view.task_request_id == api_view.task_request_id
    assert cli_view.state is api_view.state is RunState.RECEIVED
    assert cli_view.run_id.root.startswith("run_")


def test_resume_rejects_an_unknown_run_id_with_a_typed_error() -> None:
    compiler = InMemoryCompiler()

    with pytest.raises(UnknownRunError) as raised:
        compiler.resume(RunId(root="run_unknown"))

    assert raised.value.code == "unknown_run"


def test_task_request_is_strict_frozen_and_closed() -> None:
    request = make_request()

    with pytest.raises(ValidationError):
        TaskRequest.model_validate(request.model_dump() | {"unexpected": "field"})
    with pytest.raises(ValidationError):
        RequestBudget.model_validate(
            {
                "schema_version": "automarkov.request-budget.v1",
                "wall_time_seconds": "60",
                "llm_token_limit": 0,
                "tool_call_limit": 0,
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        request.task_text = "mutated"


def test_compiler_start_revalidates_a_forged_task_request() -> None:
    valid = make_request("request_forged_valid")
    constructed = TaskRequest.model_construct(
        schema_version="automarkov.task-request.v1",
        request_id=valid.request_id,
        task_text=valid.task_text,
        budget=valid.budget,
        permissions=valid.permissions,
    )

    for forged in (constructed, valid.model_copy()):
        with pytest.raises(ValueError, match="validated exact TaskRequest"):
            InMemoryCompiler().start(forged)


def test_compiler_rejects_task_requests_from_unapproved_pydantic_ingress() -> None:
    payload = make_request("request_unapproved_ingress").model_dump(mode="python")
    coerced = TaskRequest.model_validate(
        payload
        | {
            "budget": payload["budget"] | {"wall_time_seconds": "60"},
        },
        strict=False,
    )
    duplicate_json = (
        TaskRequest.model_validate(payload)
        .model_dump_json()
        .replace(
            '"request_id":"request_unapproved_ingress"',
            '"request_id":"request_first","request_id":"request_second"',
        )
    )
    duplicate_accepted_by_pydantic = TypeAdapter(TaskRequest).validate_json(
        duplicate_json,
        strict=True,
    )

    for unapproved in (coerced, duplicate_accepted_by_pydantic):
        with pytest.raises(ValueError, match="validated exact TaskRequest"):
            InMemoryCompiler().start(unapproved)


@pytest.mark.parametrize(
    "mutated_field",
    ["request", "budget", "nested-extra", "scalar-subclass"],
)
def test_compiler_rejects_mutation_after_validated_ingress(
    mutated_field: str,
) -> None:
    request = make_request(f"request_mutated_{mutated_field}")
    if mutated_field == "request":
        cast(dict[str, Any], request.__dict__)["request_id"] = (
            "request_forged_after_validation"
        )
    elif mutated_field == "budget":
        cast(dict[str, Any], request.budget.__dict__)["wall_time_seconds"] = 61
    elif mutated_field == "nested-extra":
        cast(dict[str, Any], request.budget.__dict__)["forged_extra"] = "hidden"
    else:
        class IntSubclass(int):
            pass

        cast(dict[str, Any], request.budget.__dict__)["wall_time_seconds"] = (
            IntSubclass(60)
        )

    with pytest.raises(ValueError, match="validated exact TaskRequest"):
        InMemoryCompiler().start(request)


def test_compiler_rejects_a_shallow_copy_of_validated_ingress() -> None:
    copied = copy.copy(make_request("request_shallow_copy"))

    with pytest.raises(ValueError, match="validated exact TaskRequest"):
        InMemoryCompiler().start(copied)


def test_run_view_rejects_an_artifact_id_in_the_task_request_namespace() -> None:
    with pytest.raises(ValidationError):
        RunView.model_validate(
            {
                "schema_version": "automarkov.run-view.v1",
                "run_id": RunId(root="run_namespace_check"),
                "task_request_id": "artifact_wrong_namespace",
                "state": RunState.RECEIVED,
            }
        )


def test_start_rejects_a_run_id_collision_without_overwriting_the_first_run() -> None:
    colliding_run_id = RunId(root="run_collision")
    compiler = InMemoryCompiler(
        run_id_factory=fixed_run_id_factory(colliding_run_id.root)
    )
    first_request = make_request("request_collision_first")
    second_request = make_request("request_collision_second")
    compiler.start(first_request)

    with pytest.raises(RunIdCollisionError) as raised:
        compiler.start(second_request)

    assert raised.value.code == "run_id_collision"
    assert compiler.resume(colliding_run_id).task_request_id == first_request.request_id


@pytest.mark.parametrize(
    ("operation", "expected_capability", "expected_owner_ticket"),
    [
        (
            lambda compiler, run_id: compiler.dispatch(
                CompilerDispatchRequest(
                    schema_version="automarkov.compiler-dispatch-request.v1",
                    run_id=run_id,
                    event_id="event_walking_skeleton",
                )
            ),
            "compiler.dispatch",
            "T03",
        ),
        (
            lambda compiler, run_id: compiler.package(run_id),
            "compiler.package",
            "T24",
        ),
    ],
    ids=["dispatch", "package"],
)
def test_deferred_compiler_capabilities_fail_with_ticket_ownership(
    operation: Callable[[InMemoryCompiler, RunId], object],
    expected_capability: str,
    expected_owner_ticket: str,
) -> None:
    run_id = RunId(root="run_deferred_capability")
    compiler = InMemoryCompiler(run_id_factory=fixed_run_id_factory(run_id.root))
    compiler.start(make_request("request_deferred_capability"))

    with pytest.raises(CapabilityDeferredError) as raised:
        operation(compiler, run_id)

    assert raised.value.code == "capability_deferred"
    assert raised.value.capability == expected_capability
    assert raised.value.owner_ticket == expected_owner_ticket
