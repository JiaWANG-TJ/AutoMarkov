from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from automarkov.adapters import InMemoryCompiler
from automarkov.domain import (
    RunId,
    Sha256Digest,
    TaskRequest,
    VerifiedEventHead,
    validate_task_request_payload,
)
from automarkov.lifecycle import RUN_PROJECTOR_HASH, LifecycleCommitReceipt, RunState
from automarkov.public import (
    AuthenticatedCommandContext,
    CommandAuthority,
    CommandPrincipalBinding,
)
from automarkov.repository import InMemoryArtifactRepository, SqliteArtifactRepository


def _request() -> TaskRequest:
    return validate_task_request_payload(
        {
            "schema_version": "automarkov.task-request.v1",
            "request_id": "request_t06_bootstrap",
            "task_text": "Model a finite-horizon inventory process.",
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
        }
    )


@pytest.fixture(params=("memory", "sqlite"))
def repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[
    tuple[
        InMemoryArtifactRepository | SqliteArtifactRepository,
        CommandAuthority,
    ]
]:
    authority = CommandAuthority(
        "authority_t06_bootstrap",
        (CommandPrincipalBinding("principal_orchestrator", None),),
    )
    repo = (
        InMemoryArtifactRepository(command_authority=authority)
        if request.param == "memory"
        else SqliteArtifactRepository(
            tmp_path / "t06.sqlite", command_authority=authority
        )
    )
    try:
        yield repo, authority
    finally:
        if isinstance(repo, SqliteArtifactRepository):
            repo.close()


def test_compiler_start_persists_a_signed_received_run(
    repository: tuple[
        InMemoryArtifactRepository | SqliteArtifactRepository,
        CommandAuthority,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, authority = repository
    run_id = RunId(root="run_t06_bootstrap")
    receipts: list[LifecycleCommitReceipt] = []
    original_commit = repo.commit

    def capture_commit(
        request: dict[str, object], *, context: AuthenticatedCommandContext
    ) -> object:
        result = original_commit(request, context=context)
        assert isinstance(result, LifecycleCommitReceipt)
        receipts.append(result)
        return result

    monkeypatch.setattr(repo, "commit", capture_commit)
    compiler = InMemoryCompiler(
        run_id_factory=cast_run_factory(run_id),
        repository=repo,
        command_authority=authority,
    )

    assert compiler.start(_request()) == run_id
    receipt = receipts[0]
    head = VerifiedEventHead(
        run_id=run_id,
        sequence_no=receipt.after_head.sequence_no,
        event_hash=Sha256Digest(root=receipt.after_head.event_hash),
    )
    projection = repo.project(
        run_id,
        head,
        projector_version="automarkov.run-projector.v1",
        projector_hash=Sha256Digest(root=RUN_PROJECTOR_HASH),
    )

    assert head.sequence_no == 0
    assert projection.state is RunState.RECEIVED


def cast_run_factory(run_id: RunId) -> Callable[[], RunId]:
    return lambda: run_id
