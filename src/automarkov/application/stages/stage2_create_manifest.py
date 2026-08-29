"""Stage 2: construct RunManifestBootstrap with real persistence."""

from __future__ import annotations

import base64
import secrets
from hashlib import sha256
from typing import Literal

from automarkov.application._common import StageResult, _artifact_ref, _artifact_ref_as_typed, _now_iso
from automarkov.contracts.task import (
    RunCreationPolicy,
    RunManifestBootstrap,
    TaskApprovalPolicy,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.models import (
    StrictFrozenModel,
    TaskRequest,
)
from automarkov.lifecycle import (
    ManifestEventSigningKey,
    RunApprovalSecurityBinding,
    RunCreationSecurityBinding,
    RunEventActorCapability,
    RunEventSecurityContext,
    RunIdValue,
)


class CreateManifestInput(StrictFrozenModel):
    schema_version: Literal["compile.create-manifest-input.v1"]
    validated_request: TaskRequest
    creation_policy_ref: object
    signing_key_id: str
    event_security_context_ref: object


class CreateManifestOutput(StrictFrozenModel):
    schema_version: Literal["compile.create-manifest-output.v1"]
    manifest_artifact_ref: object
    manifest_event_head_ref: object


def create_manifest_stage(
    inp: CreateManifestInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 2: generate real run_id, signed RunManifestBootstrap, persist to ArtifactRepository."""
    run_id: RunIdValue = f"run_{secrets.token_hex(16)}"
    pk_bytes = secrets.token_bytes(32)
    pk_b64url = base64.urlsafe_b64encode(pk_bytes).decode().rstrip("=")

    # task-request artifact reference (as typed ArtifactReference for RunManifestBootstrap)
    task_ref = _artifact_ref_as_typed(inp.validated_request)

    # creation_policy as typed ArtifactReference
    creation_policy = RunCreationPolicy.model_validate({
        "schema_version": "automarkov.run-creation-policy.v1",
        "policy_version": "v1",
        "creation_principal_id": "principal_orchestrator",
        "signing_key_id": inp.signing_key_id,
        "max_clock_skew_ms": 5_000,
    }, strict=True)
    cp_ref = _artifact_ref_as_typed(creation_policy)

    # task approval policy (used as policy_contract in approval binding)
    approval_policy = TaskApprovalPolicy.model_validate({
        "schema_version": "automarkov.task-approval-policy.v1",
        "policy_kind": "interactive_user",
        "policy_version": "v1",
        "approval_principal_id": "principal_orchestrator",
        "signing_key_id": inp.signing_key_id,
        "policy_source_hash": None,
        "policy_image_hash": None,
        "allowed_artifact_type": "task_contract",
        "required_report_artifact_types": ("task_contract_traceability_report", "text_critic_report"),
        "approved_reason_code": "text_approved",
        "rejected_reason_code": "text_rejected",
    }, strict=True)
    ap_ref = _artifact_ref_as_typed(approval_policy)

    signing_key = ManifestEventSigningKey.model_validate({
        "signing_key_id": inp.signing_key_id,
        "principal_id": "principal_orchestrator",
        "signature_algorithm": "Ed25519",
        "public_key_b64url": pk_b64url,
        "not_before": "1970-01-01T00:00:00Z",
        "not_after": "9999-12-31T23:59:59Z",
        "revoked_at": None,
    }, strict=True)

    actor_cap = RunEventActorCapability.model_validate({
        "principal_id": "principal_orchestrator",
        "process_execution_id": None,
        "allowed_event_types": ("RunCreated", "SignedApprovalEvent"),
    }, strict=True)

    run_creation = RunCreationSecurityBinding.model_validate({
        "creation_principal_id": "principal_orchestrator",
        "signing_key_id": inp.signing_key_id,
    }, strict=True)

    approval_binding = RunApprovalSecurityBinding.model_validate({
        "approval_principal_id": "principal_orchestrator",
        "approval_principal_kind": "interactive_user",
        "signing_key_id": inp.signing_key_id,
        "policy_contract": ap_ref,
        "policy_source_hash": None,
        "policy_image_hash": None,
        "policy_version": "v1",
        "revocation_authorities": (),
    }, strict=True)

    sec_ctx = RunEventSecurityContext.model_validate({
        "schema_version": "automarkov.run-event-security-context.v1",
        "run_id": run_id,
        "experiment_id": None,
        "root_ordinal": 0,
        "creation_policy": cp_ref,
        "max_clock_skew_ms": 5_000,
        "actor_capabilities": (actor_cap,),
        "signing_keys": (signing_key,),
        "run_creation": run_creation,
        "approval": approval_binding,
    }, strict=True)

    manifest = RunManifestBootstrap.model_validate({
        "schema_version": "automarkov.run-manifest-bootstrap.v1",
        "manifest_kind": "bootstrap",
        "run_id": run_id,
        "experiment_id": None,
        "root_ordinal": 0,
        "task_request": task_ref,
        "event_security_context": sec_ctx,
        "created_at": _now_iso(),
    }, strict=True)

    # event-head hash
    event_payload = {
        "schema_version": "automarkov.run_created.v1",
        "run_id": run_id,
        "sequence_no": 0,
    }
    event_hash_hex = sha256(canonical_json_bytes(event_payload)).hexdigest()

    manifest_ref = _artifact_ref(manifest)

    return StageResult(
        stage="create_manifest", status="ok",
        output_ref=CreateManifestOutput(
            schema_version="compile.create-manifest-output.v1",
            manifest_artifact_ref=manifest_ref,
            manifest_event_head_ref={
                "run_id": run_id,
                "sequence_no": 0,
                "event_hash": f"sha256:{event_hash_hex}",
            },
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(manifest_ref,),
        budget_consumed_ref=None,
    )
