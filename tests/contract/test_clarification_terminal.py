from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from automarkov.canonical import canonical_json_bytes
from automarkov.clarification import (
    ClarificationContinuationPolicy,
    ClarificationGap,
    ClarificationRequiredResult,
    ExperimentClarificationRequiredResult,
    SignedAnswerBundle,
    TerminalArtifactDagEntry,
    clarification_gap_id,
    recompute_terminal_artifact_dag,
    terminal_artifact_dag_hash,
)
from automarkov.domain import RunId, Sha256Digest, StrictFrozenModel, VerifiedEventHead
from automarkov.lifecycle import (
    RUN_PROJECTOR_HASH,
    RUN_PROJECTOR_VERSION,
    ArtifactReference,
    EventReference,
    TerminalResult,
)
from automarkov.repository import (
    ArtifactSchemaRegistry,
    InMemoryArtifactRepository,
    SqliteArtifactRepository,
)

_TASK_ID = "artifact_" + "1" * 64
_REVIEW_ID = "artifact_" + "2" * 64
_MASK_ID = "artifact_" + "3" * 64
_CREATED_AT = "2026-08-12T12:00:00Z"


class _DagFixtureArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.clarification-dag-fixture.v1"]
    label: str


def _dag_registry() -> ArtifactSchemaRegistry:
    registry = ArtifactSchemaRegistry()
    registry.register(
        "task_contract",
        "automarkov.clarification-dag-fixture.v1",
        _DagFixtureArtifact,
        direct_parent_artifact_types=(),
    )
    registry.register(
        "clarification_required_result",
        "automarkov.clarification-dag-fixture.v1",
        _DagFixtureArtifact,
        direct_parent_artifact_types=("task_contract",),
    )
    registry.register(
        "experiment_clarification_required_result",
        "automarkov.clarification-dag-fixture.v1",
        _DagFixtureArtifact,
        direct_parent_artifact_types=("clarification_required_result",),
    )
    registry.register(
        "environment_binding",
        "automarkov.clarification-dag-fixture.v1",
        _DagFixtureArtifact,
        direct_parent_artifact_types=(),
    )
    registry.freeze()
    return registry


@pytest.fixture(params=("memory", "sqlite"))
def dag_repository(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[InMemoryArtifactRepository | SqliteArtifactRepository]:
    repository: InMemoryArtifactRepository | SqliteArtifactRepository
    if request.param == "memory":
        repository = InMemoryArtifactRepository(_dag_registry())
    else:
        repository = SqliteArtifactRepository(
            tmp_path / "clarification-dag.sqlite", _dag_registry()
        )
    try:
        yield repository
    finally:
        if isinstance(repository, SqliteArtifactRepository):
            repository.close()


def _put_dag_artifact(
    repository: InMemoryArtifactRepository | SqliteArtifactRepository,
    artifact_type: str,
    label: str,
    parents: tuple[ArtifactReference, ...] = (),
) -> ArtifactReference:
    result = repository.put(
        {
            "schema_version": "automarkov.artifact-put-request.v2",
            "artifact_type": artifact_type,
            "payload_bytes": canonical_json_bytes(
                {
                    "schema_version": "automarkov.clarification-dag-fixture.v1",
                    "label": label,
                }
            ),
            "parent_artifact_ids": sorted(
                (parent.artifact_id for parent in parents),
                key=lambda item: item.encode("utf-8"),
            ),
            "created_by": "principal_clarification_test",
            "created_at": _CREATED_AT,
            "source_evidence_ids": [],
        }
    )
    return ArtifactReference(
        artifact_id=result.artifact_id.root,
        payload_hash=result.payload_hash.root,
    )


def _terminal_with_outputs(
    outputs: tuple[ArtifactReference, ...],
) -> TerminalResult:
    terminal_hash = "sha256:" + "4" * 64
    return TerminalResult(
        schema_version="automarkov.terminal-result.v1",
        signing_domain="AutoMarkov-TerminalResult-v1",
        run_id="run_clarification_dag",
        experiment_id="experiment_clarification",
        fixed_commit_job_manifest=ArtifactReference(
            artifact_id="artifact_" + "4" * 64,
            payload_hash="sha256:" + "5" * 64,
        ),
        process_execution_terminal_record=ArtifactReference(
            artifact_id="artifact_" + "5" * 64,
            payload_hash="sha256:" + "6" * 64,
        ),
        process_execution_id="execution_clarification_terminal",
        terminal_event=EventReference(
            event_id="019921bc-4a00-7000-8000-000000000001",
            sequence_no=7,
            event_hash=terminal_hash,
        ),
        terminal_snapshot_event_head=VerifiedEventHead(
            run_id=RunId(root="run_clarification_dag"),
            sequence_no=7,
            event_hash=Sha256Digest(root=terminal_hash),
        ),
        terminal_state="CLARIFICATION_REQUIRED",
        terminal_reason_code="clarification_required",
        payload_outputs=outputs,
        terminal_time_approvals=(),
        projector_version=RUN_PROJECTOR_VERSION,
        projector_hash=RUN_PROJECTOR_HASH,
        created_at=_CREATED_AT,
    )


def test_clarification_gap_has_stable_domain_separated_identity() -> None:
    gap = ClarificationGap(
        target_path="objective.discount_factor",
        question="What discount factor should govern the task?",
        consequence="The return objective is not yet closed.",
        evidence_ids=("evidence_public_task_card",),
    )

    assert clarification_gap_id(gap) == (
        "sha256:56c65844d32d01761c3dbd8361a16d955d935b0e51d6a54991c017a4b97072e3"
    )


def test_clarification_result_stops_before_assumptions_or_formalization() -> None:
    gap = ClarificationGap(
        target_path="objective.discount_factor",
        question="What discount factor should govern the task?",
        consequence="The return objective is not yet closed.",
        evidence_ids=(),
    )
    result = ClarificationRequiredResult(
        schema_version="automarkov.clarification-required-result.v1",
        result_kind="clarification_required",
        task_artifact_id=_TASK_ID,
        review_report_artifact_id=_REVIEW_ID,
        identified_gaps=(gap,),
        introduced_assumptions=(),
        formal_artifact_ids=(),
        environment_artifact_ids=(),
    )

    wrapped = ExperimentClarificationRequiredResult(
        schema_version="automarkov.experiment-clarification-required-result.v1",
        result_kind="experiment_clarification_required",
        clarification=result,
        outcome_mask_id=_MASK_ID,
        variant_id="v5_clarification_required",
        track="AUTO",
    )

    assert wrapped.clarification == result
    with pytest.raises(ValidationError, match="must not guess or formalize"):
        ClarificationRequiredResult(
            schema_version="automarkov.clarification-required-result.v1",
            result_kind="clarification_required",
            task_artifact_id=_TASK_ID,
            review_report_artifact_id=_REVIEW_ID,
            identified_gaps=(gap,),
            introduced_assumptions=("Assume gamma is 0.99",),
            formal_artifact_ids=(),
            environment_artifact_ids=(),
        )


def test_terminal_artifact_dag_hash_has_one_canonical_preimage() -> None:
    first_id = "artifact_" + "a" * 64
    second_id = "artifact_" + "b" * 64
    entries = (
        TerminalArtifactDagEntry(
            artifact_id=first_id,
            artifact_type="task_contract",
            payload_hash="sha256:" + "1" * 64,
            parent_artifact_ids=(),
        ),
        TerminalArtifactDagEntry(
            artifact_id=second_id,
            artifact_type="clarification_required_result",
            payload_hash="sha256:" + "2" * 64,
            parent_artifact_ids=(first_id,),
        ),
    )

    assert (
        terminal_artifact_dag_hash(
            run_id="run_clarification_vector",
            terminal_snapshot_event_head_hash="sha256:" + "3" * 64,
            artifacts=entries,
        )
        == "sha256:577b68b90f8d3fd8cf43c0f451d59fe9a912159aba360bea7d6cc8ed8f42e1a8"
    )

    with pytest.raises(ValueError, match="sorted and unique"):
        terminal_artifact_dag_hash(
            run_id="run_clarification_vector",
            terminal_snapshot_event_head_hash="sha256:" + "3" * 64,
            artifacts=tuple(reversed(entries)),
        )


def test_terminal_artifact_dag_is_recomputed_from_terminal_roots(
    dag_repository: InMemoryArtifactRepository | SqliteArtifactRepository,
) -> None:
    task = _put_dag_artifact(dag_repository, "task_contract", "task")
    result = _put_dag_artifact(
        dag_repository,
        "clarification_required_result",
        "result",
        (task,),
    )
    wrapper = _put_dag_artifact(
        dag_repository,
        "experiment_clarification_required_result",
        "wrapper",
        (result,),
    )

    closure = recompute_terminal_artifact_dag(
        dag_repository, _terminal_with_outputs((wrapper,))
    )

    assert tuple(entry.artifact_id for entry in closure.artifacts) == tuple(
        sorted(entry.artifact_id for entry in closure.artifacts)
    )
    assert {entry.artifact_type for entry in closure.artifacts} == {
        "task_contract",
        "clarification_required_result",
        "experiment_clarification_required_result",
    }
    assert closure.closure_hash == terminal_artifact_dag_hash(
        run_id="run_clarification_dag",
        terminal_snapshot_event_head_hash="sha256:" + "4" * 64,
        artifacts=closure.artifacts,
    )

    environment = _put_dag_artifact(dag_repository, "environment_binding", "forbidden")
    with pytest.raises(ValueError, match="formal or environment"):
        recompute_terminal_artifact_dag(
            dag_repository,
            _terminal_with_outputs((wrapper, environment)),
        )


def test_continuation_inputs_only_authorize_one_nonconfirmatory_child() -> None:
    policy_payload: dict[str, object] = {
        "schema_version": "automarkov.clarification-continuation-policy.v1",
        "signing_domain": "AutoMarkov-Clarification-Continuation-Policy-v1",
        "authority_principal_id": "principal_clarification_authority",
        "signing_key_id": "key_clarification_authority",
        "authority_status": "active",
        "preregistration_artifact_id": "artifact_" + "8" * 64,
        "preregistration_payload_hash": "sha256:" + "8" * 64,
        "child_ordinal_increment": 1,
        "maximum_child_count": 1,
        "experiment_eligibility": "nonconfirmatory",
        "allowed_answer_artifact_kinds": ["signed_answer_bundle"],
        "budget_reset_rule": "fresh_child_budget",
        "runtime_reset_rule": "revalidate_runtime",
        "issued_at": _CREATED_AT,
        "nonce_b64url": "A" * 43,
        "signature_algorithm": "Ed25519",
        "signature_b64url": "A" * 86,
    }
    policy = ClarificationContinuationPolicy.model_validate(policy_payload, strict=True)
    answer = SignedAnswerBundle(
        schema_version="automarkov.signed-answer-bundle.v1",
        signing_domain="AutoMarkov-Signed-Answer-Bundle-v1",
        principal_id="principal_clarification_answerer",
        signing_key_id="key_clarification_answerer",
        answer_hash="sha256:" + "9" * 64,
        preregistration_artifact_id=policy.preregistration_artifact_id,
        preregistration_payload_hash=policy.preregistration_payload_hash,
        issued_at=_CREATED_AT,
        nonce_b64url=base64.urlsafe_b64encode(b"\x01" * 32).decode().rstrip("="),
        signature_algorithm="Ed25519",
        signature_b64url="A" * 86,
    )

    assert policy.child_ordinal_increment == policy.maximum_child_count == 1
    assert answer.preregistration_artifact_id == policy.preregistration_artifact_id
    with pytest.raises(ValidationError):
        ClarificationContinuationPolicy.model_validate(
            policy_payload | {"experiment_eligibility": "confirmatory"},
            strict=True,
        )
