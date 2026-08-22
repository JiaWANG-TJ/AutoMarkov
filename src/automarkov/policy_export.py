"""T19: Policy export、safetensors manifest 与 exact-ten-seed evaluation request。

Checkpoint → (重验 tree → 导出 safetensors) → PolicyExportManifest →
PolicyEvaluationRequest (seeds 1001–1010) → sealed policy evaluation。
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from automarkov.canonical import FrozenSequence
from automarkov.domain import StrictFrozenModel
from automarkov.lifecycle import (
    ArtifactReference,
    CanonicalTimestamp,
    NonEmptyId,
    Sha256Value,
)


# ── Checkpoint tree contract ────────────────────────────────────


class CheckpointTreeEntry(StrictFrozenModel):
    relative_path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    size_bytes: Annotated[int, Field(strict=True, ge=0, le=2**40)]
    sha256: Sha256Value


class CheckpointTreeManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.checkpoint-tree-manifest.v1"] = (
        "automarkov.checkpoint-tree-manifest.v1"
    )
    entries: FrozenSequence[CheckpointTreeEntry]

    @model_validator(mode="after")
    def require_canonical_order(self) -> Self:
        paths = [e.relative_path for e in self.entries]
        if paths != sorted(paths, key=lambda p: p.encode("utf-8")):
            raise ValueError("checkpoint tree entries must be sorted by path bytes")
        if len(set(paths)) != len(paths):
            raise ValueError("checkpoint tree paths must be unique")
        return self


# ── Safetensors export ───────────────────────────────────────────


class TensorSpec(StrictFrozenModel):
    name: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    dtype: Literal["float32", "float64", "int32", "int64", "bool", "uint8"]
    shape: FrozenSequence[Annotated[int, Field(strict=True, ge=0, le=2**20)]]


class PolicyExportManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.policy-export-manifest.v1"] = (
        "automarkov.policy-export-manifest.v1"
    )
    signing_domain: Literal["AutoMarkov-PolicyExportManifest-v1"] = (
        "AutoMarkov-PolicyExportManifest-v1"
    )
    export_id: NonEmptyId
    experiment_id: NonEmptyId
    run_id: NonEmptyId
    candidate_bundle: ArtifactReference
    seed: Annotated[int, Field(strict=True, ge=1001, le=1010)]
    training_terminal_record: ArtifactReference
    export_terminal_record: ArtifactReference
    source_checkpoint_commitment: Sha256Value
    architecture_id: NonEmptyId
    connector_id: NonEmptyId
    observation_adapter_id: NonEmptyId
    action_adapter_id: NonEmptyId
    trainer_execution_id: NonEmptyId
    exporter_execution_id: NonEmptyId
    tensor_artifact: ArtifactReference
    tensors: FrozenSequence[TensorSpec]
    issued_at: CanonicalTimestamp
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$")]
    signature_algorithm: Literal["Ed25519"] = "Ed25519"
    principal_id: NonEmptyId
    signing_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_closed_export(self) -> Self:
        tensor_names = [t.name for t in self.tensors]
        if len(set(tensor_names)) != len(tensor_names):
            raise ValueError("tensor names must be unique")
        if not tensor_names:
            raise ValueError("export manifest must contain at least one tensor")
        return self


# ── Policy evaluation request ───────────────────────────────────


_SeedValue = Annotated[int, Field(strict=True, ge=1001, le=1010)]
CANONICAL_TEN_SEEDS: tuple[int, ...] = tuple(range(1001, 1011))


class PolicyEvaluationSeedBinding(StrictFrozenModel):
    seed: _SeedValue
    branch: Literal["success", "training_failure", "export_failure"]
    training_terminal_record: ArtifactReference
    export_terminal_record: ArtifactReference | None = None
    export_manifest: ArtifactReference | None = None
    tensor_artifact: ArtifactReference | None = None

    @model_validator(mode="after")
    def require_branch_cardinality(self) -> Self:
        if self.branch == "success":
            if (
                self.export_terminal_record is None
                or self.export_manifest is None
                or self.tensor_artifact is None
            ):
                raise ValueError("success branch must bind all export fields")
        elif self.branch == "training_failure":
            if (
                self.export_terminal_record is not None
                or self.export_manifest is not None
                or self.tensor_artifact is not None
            ):
                raise ValueError(
                    "training_failure branch must have empty export fields"
                )
        elif self.branch == "export_failure":
            if self.export_manifest is not None or self.tensor_artifact is not None:
                raise ValueError(
                    "export_failure branch must have empty manifest/tensor fields"
                )
            if self.export_terminal_record is None:
                raise ValueError(
                    "export_failure branch must bind export terminal record"
                )
        return self


class PolicyEvaluationRequest(StrictFrozenModel):
    schema_version: Literal["automarkov.policy-evaluation-request.v1"] = (
        "automarkov.policy-evaluation-request.v1"
    )
    signing_domain: Literal["AutoMarkov-PolicyEvaluationRequest-v1"] = (
        "AutoMarkov-PolicyEvaluationRequest-v1"
    )
    request_id: NonEmptyId
    experiment_id: NonEmptyId
    run_id: NonEmptyId
    candidate_bundle: ArtifactReference
    run_manifest: ArtifactReference
    e2e_verdict: ArtifactReference
    smoke_attestation: ArtifactReference
    suite_calibration: ArtifactReference
    evaluator_profile_id: NonEmptyId
    evaluator_profile_hash: Sha256Value
    observation_adapter_id: NonEmptyId
    action_adapter_id: NonEmptyId
    seed_bindings: FrozenSequence[PolicyEvaluationSeedBinding]
    issued_at: CanonicalTimestamp
    not_before: CanonicalTimestamp
    expires_at: CanonicalTimestamp
    nonce_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$")]
    signature_algorithm: Literal["Ed25519"] = "Ed25519"
    coordinator_principal_id: NonEmptyId
    coordinator_key_id: NonEmptyId
    signature_b64url: Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$")]

    @model_validator(mode="after")
    def require_exact_ten_seeds(self) -> Self:
        seeds = tuple(b.seed for b in self.seed_bindings)
        if seeds != CANONICAL_TEN_SEEDS:
            raise ValueError("seed bindings must be exactly 1001 through 1010 in order")
        return self

    @property
    def success_count(self) -> int:
        return sum(1 for b in self.seed_bindings if b.branch == "success")

    @property
    def failure_count(self) -> int:
        return len(self.seed_bindings) - self.success_count


# ── Post-training sealed evaluation outcome ─────────────────────


class PolicyEvaluationOutcome(StrictFrozenModel):
    seed: _SeedValue
    branch: Literal["success", "training_failure", "export_failure"]
    gold_policy_evaluation_valid: bool = Field(strict=True)
    q_gate: float = Field(strict=True)
    normalized_return: float | None = None
    failure_reason: Annotated[str | None, Field(strict=True, max_length=1024)] = None

    @model_validator(mode="after")
    def require_failure_mapping(self) -> Self:
        if self.branch != "success":
            if self.gold_policy_evaluation_valid or self.q_gate != 0.0:
                raise ValueError(
                    "non-success branches must have GoldPolicyEvaluationValid=0, Q_gate=0"
                )
            if self.normalized_return is not None:
                raise ValueError(
                    "non-success branches must not report normalized_return"
                )
        return self


# ── 导出 ────────────────────────────────────────────────────────


__all__ = [
    "CANONICAL_TEN_SEEDS",
    "CheckpointTreeEntry",
    "CheckpointTreeManifest",
    "PolicyEvaluationOutcome",
    "PolicyEvaluationRequest",
    "PolicyEvaluationSeedBinding",
    "PolicyExportManifest",
    "TensorSpec",
]
