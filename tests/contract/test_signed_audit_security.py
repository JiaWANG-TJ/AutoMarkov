from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
import test_cross_run_repository as cross_run

from automarkov.errors import (
    CommandAuthenticationError,
    EventReplayConflictError,
    TerminalProvenanceError,
    UnknownArtifactError,
)
from automarkov.lifecycle import ZERO_EVENT_HASH, LifecycleCommitReceipt
from automarkov.repository import (
    InMemoryArtifactRepository,
    SqliteArtifactRepository,
)

ArtifactRepositoryAdapter = InMemoryArtifactRepository | SqliteArtifactRepository

_EXPERIMENT_ID = "experiment_signed_audit_security"
_LATE_RECEIVED_AT = "2026-08-10T13:00:00Z"


def _uuid7_at(issued_at: str, index: int) -> str:
    timestamp_ms = int(datetime.fromisoformat(issued_at).timestamp() * 1_000)
    return str(UUID(int=(timestamp_ms << 80) | (7 << 76) | (2 << 62) | index))


@pytest.fixture(params=("memory", "sqlite"))
def repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[ArtifactRepositoryAdapter]:
    if request.param == "memory":
        result: ArtifactRepositoryAdapter = InMemoryArtifactRepository(
            cross_run._registry(),
            command_authority=cross_run._AUTHORITY,
        )
    else:
        result = SqliteArtifactRepository(
            tmp_path / "signed-audit.sqlite",
            cross_run._registry(),
            command_authority=cross_run._AUTHORITY,
        )
    try:
        yield result
    finally:
        if isinstance(result, SqliteArtifactRepository):
            result.close()


def _put_fixture(
    repository: ArtifactRepositoryAdapter,
    artifact_type: str,
    name: str,
) -> Any:
    return cross_run._put(
        repository,
        artifact_type,
        {"schema_version": "automarkov.cross-run-fixture.v1", "name": name},
    )


def _start_signed_audit_run(
    repository: ArtifactRepositoryAdapter,
    *,
    revoked_at: str | None = None,
    max_clock_skew_ms: int = 0,
) -> tuple[LifecycleCommitReceipt, dict[str, Any]]:
    governance = _put_fixture(repository, "governance_report", "governance")
    approval = _put_fixture(repository, "governance_report", "approval")
    plan = _put_fixture(repository, "governance_report", "ablation-plan")
    task_card = _put_fixture(repository, "governance_report", "task-card")
    output = _put_fixture(repository, "payload_output", "omission-output")
    governance_ref = cross_run._reference(governance)
    approval_ref = cross_run._reference(approval)
    security_context = cross_run._security_context(
        cross_run._PARENT_RUN_ID,
        0,
        governance_ref,
        approval_ref,
    )
    security_context["experiment_id"] = _EXPERIMENT_ID
    security_context["max_clock_skew_ms"] = max_clock_skew_ms
    capabilities = cast(list[dict[str, object]], security_context["actor_capabilities"])
    capabilities[0]["allowed_event_types"] = [
        "ExecutionTopologySubstituted",
        "GateOmittedByDesign",
        "RunCreated",
    ]
    signing_keys = cast(list[dict[str, object]], security_context["signing_keys"])
    signing_keys[0]["revoked_at"] = revoked_at
    manifest = cross_run._put(
        repository,
        "run_manifest",
        {
            "schema_version": "automarkov.cross-run-manifest.v1",
            "event_security_context": security_context,
            "replacement_ordinal": 0,
            "clarification_continuation_ordinal": 0,
            "replacement_policy": governance_ref,
            "parent_run_id": None,
            "parent_run_superseded_event_id": None,
            "supersession_cause": None,
        },
    )
    root_event = cross_run._sign_payload(
        {
            "schema_version": "automarkov.run-created.v1",
            "event_type": "RunCreated",
            "signing_domain": "AutoMarkov-Run-Created-v1",
            "event_id": cross_run._uuid7(501),
            "experiment_id": _EXPERIMENT_ID,
            "run_id": cross_run._PARENT_RUN_ID,
            "actor_principal_id": cross_run._PRINCIPAL_ID,
            "issued_at": cross_run._ISSUED_AT,
            "sequence_no": 0,
            "previous_event_hash": ZERO_EVENT_HASH,
            "run_manifest_artifact_id": manifest.artifact_id.root,
            "run_manifest_payload_hash": manifest.payload_hash.root,
            "initial_state": "RECEIVED",
            "creation_principal_id": cross_run._PRINCIPAL_ID,
            "reason_code": "run_created",
            "nonce_b64url": cross_run._nonce(501),
            "signing_key_id": cross_run._SIGNING_KEY_ID,
            "signature_algorithm": "Ed25519",
        }
    )
    committed = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": cross_run._uuid7(502),
            "actor_principal_id": cross_run._PRINCIPAL_ID,
            "issued_at": cross_run._ISSUED_AT,
            "idempotency_key": "signed-audit-root",
            "run_id": cross_run._PARENT_RUN_ID,
            "expected_state": None,
            "expected_head": None,
            "events": [root_event],
        },
        context=cross_run._AUTHORITY.issue(
            cross_run._PRINCIPAL_ID,
            None,
            cross_run._ISSUED_AT,
        ),
    )
    assert isinstance(committed, LifecycleCommitReceipt)
    return committed, {
        "plan": plan,
        "task_card": task_card,
        "output": output,
    }


def _gate_event(
    *,
    head: LifecycleCommitReceipt,
    artifacts: dict[str, Any],
    index: int,
    nonce_index: int,
    plan_hash: str | None = None,
    task_card_artifact_id: str | None = None,
) -> dict[str, object]:
    plan = cross_run._reference(artifacts["plan"])
    return cross_run._sign_payload(
        {
            "schema_version": "automarkov.gate-omitted-event.v1",
            "event_type": "GateOmittedByDesign",
            "signing_domain": "AutoMarkov-Gate-Omitted-v1",
            "event_id": cross_run._uuid7(index),
            "experiment_id": _EXPERIMENT_ID,
            "run_id": cross_run._PARENT_RUN_ID,
            "sequence_no": head.after_head.sequence_no + 1,
            "previous_event_hash": head.after_head.event_hash,
            "track": "AUTO",
            "variant_id": "v1_canonical",
            "cell_id": "cell_signed_audit",
            "ablation_execution_plan_artifact_id": plan["artifact_id"],
            "ablation_execution_plan_hash": plan_hash or plan["payload_hash"],
            "pair_binding_id": "pair_signed_audit",
            "task_card_artifact_id": task_card_artifact_id
            or artifacts["task_card"].artifact_id.root,
            "subject_artifact_ids": [],
            "expected_missing_artifact_kinds": ["EvidenceLedger"],
            "output_artifact_ids": [artifacts["output"].artifact_id.root],
            "reason": "controlled_ablation",
            "ablation_method_id": "automarkov_no_evidence",
            "omitted_gate_id": "EVIDENCE_LEDGER_CLOSURE",
            "issued_at": cross_run._ISSUED_AT,
            "nonce_b64url": cross_run._nonce(nonce_index),
            "signing_key_id": cross_run._SIGNING_KEY_ID,
        }
    )


def _topology_event(
    *,
    head: LifecycleCommitReceipt,
    artifacts: dict[str, Any],
    nonce_index: int,
) -> dict[str, object]:
    plan = cross_run._reference(artifacts["plan"])
    return cross_run._sign_payload(
        {
            "schema_version": "automarkov.execution-topology-substituted.v1",
            "event_type": "ExecutionTopologySubstituted",
            "signing_domain": "AutoMarkov-Execution-Topology-Substituted-v1",
            "event_id": cross_run._uuid7(505),
            "experiment_id": _EXPERIMENT_ID,
            "run_id": cross_run._PARENT_RUN_ID,
            "sequence_no": head.after_head.sequence_no + 1,
            "previous_event_hash": head.after_head.event_hash,
            "ablation_execution_plan_artifact_id": plan["artifact_id"],
            "ablation_execution_plan_hash": plan["payload_hash"],
            "ablation_method_id": "automarkov_single_agent_workflow",
            "cell_id": "cell_signed_audit",
            "from_topology": "multi_role",
            "to_topology": "single_qwen_sequential",
            "role_order": ["researcher", "formalizer"],
            "prompt_hashes": ["sha256:" + "1" * 64, "sha256:" + "2" * 64],
            "model_identity_hash": "sha256:" + "3" * 64,
            "issued_at": cross_run._ISSUED_AT,
            "nonce_b64url": cross_run._nonce(nonce_index),
            "signing_key_id": cross_run._SIGNING_KEY_ID,
        }
    )


def _append_audit(
    repository: ArtifactRepositoryAdapter,
    head: LifecycleCommitReceipt,
    event: dict[str, object],
    *,
    command_index: int,
    received_at: str = cross_run._ISSUED_AT,
) -> LifecycleCommitReceipt:
    result = repository.commit(
        {
            "schema_version": "automarkov.lifecycle-command.v1",
            "command_type": "append_run_events",
            "command_id": _uuid7_at(received_at, command_index),
            "actor_principal_id": cross_run._PRINCIPAL_ID,
            "issued_at": received_at,
            "idempotency_key": f"signed-audit-command-{command_index}",
            "run_id": cross_run._PARENT_RUN_ID,
            "expected_state": "RECEIVED",
            "expected_head": head.after_head.model_dump(mode="json"),
            "events": [event],
        },
        context=cross_run._AUTHORITY.issue(
            cross_run._PRINCIPAL_ID,
            None,
            received_at,
        ),
    )
    assert isinstance(result, LifecycleCommitReceipt)
    return result


def test_signed_audit_nonce_replay_is_rejected_globally(
    repository: ArtifactRepositoryAdapter,
) -> None:
    root, artifacts = _start_signed_audit_run(repository)
    nonce_index = 503
    gate = _gate_event(
        head=root,
        artifacts=artifacts,
        index=503,
        nonce_index=nonce_index,
    )
    gate_commit = _append_audit(repository, root, gate, command_index=503)
    topology = _topology_event(
        head=gate_commit,
        artifacts=artifacts,
        nonce_index=nonce_index,
    )

    with pytest.raises(EventReplayConflictError):
        _append_audit(repository, gate_commit, topology, command_index=505)


def test_signed_audit_rejects_plan_hash_substitution(
    repository: ArtifactRepositoryAdapter,
) -> None:
    root, artifacts = _start_signed_audit_run(repository)
    event = _gate_event(
        head=root,
        artifacts=artifacts,
        index=506,
        nonce_index=506,
        plan_hash="sha256:" + "f" * 64,
    )

    with pytest.raises(TerminalProvenanceError):
        _append_audit(repository, root, event, command_index=506)


def test_gate_omission_requires_the_task_card_artifact(
    repository: ArtifactRepositoryAdapter,
) -> None:
    root, artifacts = _start_signed_audit_run(repository)
    event = _gate_event(
        head=root,
        artifacts=artifacts,
        index=507,
        nonce_index=507,
        task_card_artifact_id="artifact_" + "f" * 64,
    )

    with pytest.raises(UnknownArtifactError):
        _append_audit(repository, root, event, command_index=507)


@pytest.mark.parametrize("missing_field", ("subject", "output"))
def test_gate_omission_keeps_subject_and_output_existence_checks(
    repository: ArtifactRepositoryAdapter,
    missing_field: str,
) -> None:
    root, artifacts = _start_signed_audit_run(repository)
    raw = _gate_event(
        head=root,
        artifacts=artifacts,
        index=509,
        nonce_index=509,
    )
    raw.pop("signature_b64url")
    missing_artifact_id = "artifact_" + "e" * 64
    if missing_field == "subject":
        raw |= {
            "ablation_method_id": "automarkov_no_text_critic",
            "omitted_gate_id": "TEXT_CRITIC_REVIEW",
            "subject_artifact_ids": [missing_artifact_id],
            "expected_missing_artifact_kinds": ["TextCriticReport"],
            "output_artifact_ids": [],
        }
    else:
        raw["output_artifact_ids"] = [missing_artifact_id]
    event = cross_run._sign_payload(raw)

    with pytest.raises(UnknownArtifactError):
        _append_audit(repository, root, event, command_index=509)


def test_signed_audit_rejects_backdated_event_received_after_key_revocation(
    repository: ArtifactRepositoryAdapter,
) -> None:
    root, artifacts = _start_signed_audit_run(
        repository,
        revoked_at="2026-08-10T12:30:00Z",
        max_clock_skew_ms=7_200_000,
    )
    event = _gate_event(
        head=root,
        artifacts=artifacts,
        index=508,
        nonce_index=508,
    )

    with pytest.raises(CommandAuthenticationError):
        _append_audit(
            repository,
            root,
            event,
            command_index=508,
            received_at=_LATE_RECEIVED_AT,
        )


@pytest.mark.parametrize(
    ("fixture_name", "event_field", "process_execution_id"),
    (
        (
            "_replacement_fixture",
            "replacement_run_created_event",
            cross_run._PROCESS_ID,
        ),
        (
            "_clarification_fixture",
            "clarification_child_run_created_event",
            None,
        ),
    ),
)
def test_cross_run_child_ingress_uses_the_child_manifest_clock(
    repository: ArtifactRepositoryAdapter,
    fixture_name: str,
    event_field: str,
    process_execution_id: str | None,
) -> None:
    fixture = cast(Any, getattr(cross_run, fixture_name))
    command, _ = fixture(repository)
    changed = deepcopy(command)
    child_event = deepcopy(cast(dict[str, object], changed[event_field]))
    child_event.pop("signature_b64url")
    child_event["issued_at"] = "2026-08-10T12:00:00.000001Z"
    changed[event_field] = cross_run._sign_payload(child_event)

    with pytest.raises(CommandAuthenticationError):
        repository.commit(
            changed,
            context=cross_run._AUTHORITY.issue(
                cross_run._PRINCIPAL_ID,
                process_execution_id,
                cross_run._ISSUED_AT,
            ),
        )
