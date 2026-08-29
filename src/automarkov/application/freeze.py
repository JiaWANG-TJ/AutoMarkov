"""R09: Freeze gate and experiment preflight coordinator.

Checks that the ExperimentClosedSet satisfies every predicate in the
15-item checklist before an experiment may proceed to training.
Zero I/O -- all checks are deterministic contracts against immutable data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from automarkov.contracts.task import (
    RunManifest,
    TaskContract,
    validate_task_contract_for_approval,
)
from automarkov.domain.canonical import FrozenSequence
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import (
    ArtifactReference,
)

PredicateKind = Literal[
    "plan_closed",
    "source_commit_present",
    "profiles_present",
    "task_cards_present",
    "methods_present",
    "eligibility_present",
    "pair_seed_ledger_present",
    "design_power_sufficient",
    "calibrations_present",
    "keys_present",
    "sealed_handshake_consistent",
    "runner_dry_run_ready",
    "remote_env_vectors_present",
    "analysis_fixtures_present",
    "replacement_policy_present",
]


class PredicateVerdict(StrictFrozenModel):
    """Single predicate outcome on the ExperimentClosedSet."""

    schema_version: Literal["automarkov.predicate-verdict.v1"] = (
        "automarkov.predicate-verdict.v1"
    )
    predicate_name: PredicateKind
    is_satisfied: bool = Field(strict=True)
    detail: str

    @model_validator(mode="after")  # type: ignore[arg-type]
    def require_nonblank_detail(self) -> object:
        if not self.detail.strip():
            raise ValueError("predicate detail must be nonblank")
        return self


class FreezeGateReport(StrictFrozenModel):
    """Typed result of all 15 freeze-gate predicates."""

    schema_version: Literal["automarkov.freeze-gate-report.v1"] = (
        "automarkov.freeze-gate-report.v1"
    )
    is_frozen: bool = Field(strict=True)
    total_predicates: int
    satisfied_count: int
    missing_count: int
    frozen_completeness: float
    predicates: FrozenSequence[PredicateVerdict]
    missing_fields: FrozenSequence[str]
    blocking_reasons: FrozenSequence[str]

    @model_validator(mode="after")  # type: ignore[arg-type]
    def require_cardinality(self) -> object:
        if self.total_predicates < 1:
            raise ValueError("freeze gate must declare at least one predicate")
        if self.satisfied_count < 0 or self.missing_count < 0:
            raise ValueError("predicate counts must be nonnegative")
        if self.satisfied_count + self.missing_count != self.total_predicates:
            raise ValueError("satisfied and missing counts must sum to total")
        if self.frozen_completeness < 0.0 or self.frozen_completeness > 1.0:
            raise ValueError("frozen completeness must be between 0.0 and 1.0")
        names = tuple(item.predicate_name for item in self.predicates)
        if len(names) != self.total_predicates:
            raise ValueError("predicate count does not match verdicts length")
        if len(set(names)) != len(names):
            raise ValueError("predicate names must be unique")
        if self.is_frozen != (self.missing_count == 0):
            raise ValueError(
                "is_frozen is True only when all predicates are satisfied"
            )
        return self


_LEVEL_RANK: dict[str, int] = {
    "schema": 0,
    "structural": 1,
    "executable": 2,
    "behavioral": 3,
    "oracle_equivalent": 4,
    "formally_verified": 5,
}


def _id_ok(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _str_nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


class FreezeGateChecker:
    """Runs 15 predicates against the ExperimentClosedSet.

    Zero I/O -- every predicate is pure validation on immutable
    TaskContract, RunManifest, and authorization data.
    """

    def __init__(
        self,
        contract: TaskContract,
        manifest: RunManifest,
        authorization: object,
    ) -> None:
        self._contract = contract
        self._manifest = manifest
        self._authorization = authorization

    def _verdict(
        self, name: PredicateKind, ok: bool, detail: str
    ) -> PredicateVerdict:
        return PredicateVerdict(
            predicate_name=name, is_satisfied=ok, detail=detail
        )

    def _check_plan_closed(self) -> PredicateVerdict:
        try:
            validate_task_contract_for_approval(self._contract.model_dump(mode="json"))
            return self._verdict("plan_closed", True, "TaskContract validation passed")
        except ValueError as exc:
            return self._verdict("plan_closed", False, str(exc))

    def _check_source_commit_present(self) -> PredicateVerdict:
        val = getattr(self._authorization, "source_commit", "")
        ok = _str_nonblank(val)
        return self._verdict(
            "source_commit_present", ok,
            "source commit present" if ok else "source commit missing",
        )

    def _check_profiles_present(self) -> PredicateVerdict:
        val = getattr(self._authorization, "profile_id", "")
        ok = _str_nonblank(val)
        return self._verdict(
            "profiles_present", ok,
            "profile ID present" if ok else "profile ID missing",
        )

    def _check_task_cards_present(self) -> PredicateVerdict:
        ref = self._manifest.task_request
        ok = (
            isinstance(ref, ArtifactReference)
            and bool(ref.artifact_id)
            and bool(ref.payload_hash)
        )
        return self._verdict(
            "task_cards_present", ok,
            "task request present" if ok else "task request missing",
        )

    def _check_methods_present(self) -> PredicateVerdict:
        val = getattr(self._authorization, "method_id", "")
        ok = _id_ok(val)
        return self._verdict(
            "methods_present", ok,
            "method ID present" if ok else "method ID missing",
        )

    def _check_eligibility_present(self) -> PredicateVerdict:
        sid = getattr(self._authorization, "suite_id", "")
        vid = getattr(self._authorization, "variant_id", "")
        ok = _id_ok(sid) and _id_ok(vid)
        return self._verdict(
            "eligibility_present", ok,
            "suite and variant present" if ok else "suite or variant missing",
        )

    def _check_pair_seed_ledger_present(self) -> PredicateVerdict:
        pid = getattr(self._authorization, "pair_id", "")
        seed = getattr(self._authorization, "generation_seed", None)
        ok = _id_ok(pid) and seed is not None
        return self._verdict(
            "pair_seed_ledger_present", ok,
            "pair and seed present" if ok else "pair or seed missing",
        )

    def _check_design_power_sufficient(self) -> PredicateVerdict:
        return self._verdict(
            "design_power_sufficient",
            False,
            "typed signed DesignPowerReport resolution is not implemented",
        )

    def _check_calibrations_present(self) -> PredicateVerdict:
        return self._verdict(
            "calibrations_present",
            False,
            "typed GoldScoreCalibration report resolution is not implemented",
        )

    def _check_keys_present(self) -> PredicateVerdict:
        grant = getattr(self._authorization, "runner_key_grant", None)
        key = getattr(grant, "signing_key_id", "") if grant is not None else ""
        ok = _str_nonblank(key)
        return self._verdict(
            "keys_present", ok,
            "signing key present" if ok else "signing key missing",
        )

    def _check_sealed_handshake_consistent(self) -> PredicateVerdict:
        return self._verdict(
            "sealed_handshake_consistent",
            False,
            "verified sealed handshake report resolution is not implemented",
        )

    def _check_runner_dry_run_ready(self) -> PredicateVerdict:
        ver = getattr(self._manifest, "schema_version", "")
        kind = getattr(self._manifest, "manifest_kind", "")
        ok = ver == "automarkov.run-manifest.v2" and kind == "frozen_run"
        return self._verdict(
            "runner_dry_run_ready", ok,
            "manifest frozen_run v2" if ok else "manifest not frozen_run v2",
        )

    def _check_remote_env_vectors_present(self) -> PredicateVerdict:
        return self._verdict(
            "remote_env_vectors_present",
            False,
            "verified RemoteEnv vector report resolution is not implemented",
        )

    def _check_analysis_fixtures_present(self) -> PredicateVerdict:
        refs = getattr(self._authorization, "input_artifacts", ())
        ok = bool(refs)
        return self._verdict(
            "analysis_fixtures_present", ok,
            f"{len(refs)} input artifact(s)" if ok else "no input artifacts",
        )

    def _check_replacement_policy_present(self) -> PredicateVerdict:
        return self._verdict(
            "replacement_policy_present",
            False,
            "typed replacement-policy artifact resolution is not implemented",
        )

    def check(self) -> FreezeGateReport:
        """Run all 15 predicates and return typed report."""
        predicates: list[PredicateVerdict] = [
            self._check_plan_closed(),
            self._check_source_commit_present(),
            self._check_profiles_present(),
            self._check_task_cards_present(),
            self._check_methods_present(),
            self._check_eligibility_present(),
            self._check_pair_seed_ledger_present(),
            self._check_design_power_sufficient(),
            self._check_calibrations_present(),
            self._check_keys_present(),
            self._check_sealed_handshake_consistent(),
            self._check_runner_dry_run_ready(),
            self._check_remote_env_vectors_present(),
            self._check_analysis_fixtures_present(),
            self._check_replacement_policy_present(),
        ]
        satisfied = sum(1 for p in predicates if p.is_satisfied)
        total = len(predicates)
        missing = total - satisfied
        missing_fields = tuple(
            p.predicate_name for p in predicates if not p.is_satisfied
        )
        blocking = tuple(p.detail for p in predicates if not p.is_satisfied)
        completeness = satisfied / total if total else 0.0
        return FreezeGateReport(
            is_frozen=(missing == 0),
            total_predicates=total,
            satisfied_count=satisfied,
            missing_count=missing,
            frozen_completeness=completeness,
            predicates=tuple(predicates),
            missing_fields=missing_fields,
            blocking_reasons=blocking,
        )


class ExperimentPreflightReport(StrictFrozenModel):
    """Derived preflight combining freeze gate and extra checks."""

    schema_version: Literal["automarkov.experiment-preflight-report.v1"] = (
        "automarkov.experiment-preflight-report.v1"
    )
    freeze_gate: FreezeGateReport
    manifest_version_valid: bool = Field(strict=True)
    e2e_authorities_consistent: bool = Field(strict=True)
    all_signing_keys_bound: bool = Field(strict=True)
    is_ready: bool = Field(strict=True)
    blocking_reasons: FrozenSequence[str]

    @model_validator(mode="after")  # type: ignore[arg-type]
    def require_ready_consistency(self) -> object:
        combined = (
            self.freeze_gate.is_frozen
            and self.manifest_version_valid
            and self.e2e_authorities_consistent
            and self.all_signing_keys_bound
        )
        if self.is_ready != combined:
            raise ValueError(
                "is_ready must be True only when all sub-checks pass"
            )
        return self


def check_freeze_gate(
    contract: TaskContract,
    manifest: RunManifest,
    authorization: object,
) -> FreezeGateReport:
    """Run the 15-predicate freeze gate against the closed set."""
    return FreezeGateChecker(contract, manifest, authorization).check()


def preflight_experiment(
    contract: TaskContract,
    manifest: RunManifest,
    authorization: object,
) -> ExperimentPreflightReport:
    """Produce a full preflight report combining freeze gate and extra checks."""
    gate = check_freeze_gate(contract, manifest, authorization)
    manifest_ok = manifest.schema_version == "automarkov.run-manifest.v2"
    e2e_ok = bool(manifest.sealed_e2e_signing_authorities)
    keys_bound = bool(getattr(manifest, "event_security_context", None))
    ready = gate.is_frozen and manifest_ok and e2e_ok and keys_bound
    reasons: list[str] = list(gate.blocking_reasons)
    if not manifest_ok:
        reasons.append("manifest schema version is not v2")
    if not e2e_ok:
        reasons.append("no sealed E2E signing authorities")
    if not keys_bound:
        reasons.append("event security context missing signing keys")
    return ExperimentPreflightReport(
        freeze_gate=gate,
        manifest_version_valid=manifest_ok,
        e2e_authorities_consistent=e2e_ok,
        all_signing_keys_bound=keys_bound,
        is_ready=ready,
        blocking_reasons=tuple(reasons),
    )


__all__ = [
    "ExperimentPreflightReport",
    "FreezeGateChecker",
    "FreezeGateReport",
    "PredicateKind",
    "PredicateVerdict",
    "check_freeze_gate",
    "preflight_experiment",
]
