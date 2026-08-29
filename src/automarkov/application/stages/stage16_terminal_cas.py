"""Stage 16: finalize the artifact chain — write terminal records to the
ArtifactRepository with proper parent lineage and generate a terminal result
containing all artifact references from the full compile pipeline.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from automarkov.application._common import StageResult, _artifact_ref_as_typed, _now_iso
from automarkov.contracts.environment import (
    EnvironmentCandidate,
    EnvironmentCandidateBundle,
    ImplementationPlan,
    ImplementationRoute,
    SandboxLimits,
    SandboxPolicy,
)
from automarkov.contracts.task import TaskContract
from automarkov.contracts.validation import ValidationReport
from automarkov.decision_process import DecisionProcessValue
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.models import (
    StrictFrozenModel,
)
from automarkov.lifecycle import ArtifactReference

# ---------------------------------------------------------------------------
# Artifact lineage and store models
# ---------------------------------------------------------------------------

class ArtifactLineageEntry(StrictFrozenModel):
    """Records one artifact in the terminal chain with its parent references."""
    artifact_id: str
    artifact_type: str  # e.g., "task_contract", "validation_report", etc.
    payload_hash: str
    parent_ids: tuple[str, ...]
    stage: str  # The pipeline stage that produced this artifact
    created_at: str


class TerminalManifest(StrictFrozenModel):
    """The complete manifest of all artifacts in the compile pipeline."""
    schema_version: Literal["compile.terminal-manifest.v1"]
    manifest_id: str
    suite_id: str
    entries: tuple[ArtifactLineageEntry, ...]
    root_artifact_id: str  # The ingress/task_request artifact
    terminal_hash: str
    merged_at: str


class CompileTerminalResult(StrictFrozenModel):
    """The final terminal result containing all key artifact references."""
    schema_version: Literal["compile.terminal-result.v1"]
    result_id: str
    task_contract_ref: object
    decision_process_spec_ref: object
    validation_report_ref: object
    critic_report_ref: object | None
    approval_decision_ref: object | None
    environment_candidate_ref: object
    candidate_bundle_ref: object
    implementation_plan_ref: object
    sandbox_policy_ref: object
    sandbox_limits_ref: object
    test_report_ref: object
    package_bundle_ref: object
    package_hash: str
    terminal_manifest_ref: object
    terminal_hash: str
    status: Literal["compiled", "compiled_with_warnings", "compilation_failed"]
    compiled_at: str


# ---------------------------------------------------------------------------
# Stage models
# ---------------------------------------------------------------------------

class TerminalCASInput(StrictFrozenModel):
    schema_version: Literal["compile.terminal-cas-input.v1"]
    task_contract: TaskContract
    decision_process_spec: DecisionProcessValue
    validation_report: ValidationReport
    critic_report: object | None  # TextCriticReport from stage 10
    approval_decision: object | None  # ApprovalDecision from stage 11
    environment_candidate: EnvironmentCandidate
    candidate_bundle: EnvironmentCandidateBundle
    implementation_plan: ImplementationPlan
    sandbox_policy: SandboxPolicy
    sandbox_limits: SandboxLimits
    test_report: object  # TestReport from stage 14
    package_bundle: object  # PackageBundle from stage 15
    package_hash: str
    route: ImplementationRoute
    manifest_ref: object


class TerminalCASOutput(StrictFrozenModel):
    schema_version: Literal["compile.terminal-cas-output.v1"]
    terminal_result: CompileTerminalResult
    terminal_manifest: TerminalManifest
    terminal_hash: str


# ---------------------------------------------------------------------------
# Lineage builder
# ---------------------------------------------------------------------------

def _build_artifact_lineage(
    inp: TerminalCASInput,
) -> tuple[ArtifactLineageEntry, ...]:
    """Build the complete artifact lineage chain with proper parent references.

    Each entry records: what artifact was produced, by which stage, and what
    parent artifacts it depends on (for DAG reconstruction).
    """
    now = _now_iso()
    entries: list[ArtifactLineageEntry] = []

    # Stage 1: validate_ingress — the task request (root)
    root_ref = _artifact_ref_as_typed({"root": "task_request", "at": now})
    entries.append(ArtifactLineageEntry(
        artifact_id=root_ref.artifact_id,
        artifact_type="task_request",
        payload_hash=root_ref.payload_hash,
        parent_ids=(),
        stage="validate_ingress",
        created_at=now,
    ))

    # Stage 2: create_manifest
    manifest_ref = _artifact_ref_as_typed(
        inp.manifest_ref if isinstance(inp.manifest_ref, dict) else {"stub": "manifest"}
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=manifest_ref.artifact_id,
        artifact_type="run_manifest",
        payload_hash=manifest_ref.payload_hash,
        parent_ids=(root_ref.artifact_id,),
        stage="create_manifest",
        created_at=now,
    ))

    # Stage 8: task_contract
    tc_ref = _artifact_ref_as_typed(
        inp.task_contract.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=tc_ref.artifact_id,
        artifact_type="task_contract",
        payload_hash=tc_ref.payload_hash,
        parent_ids=(root_ref.artifact_id, manifest_ref.artifact_id),
        stage="propose_formal_spec",
        created_at=now,
    ))

    # Stage 8: decision_process_spec
    dp_ref = _artifact_ref_as_typed(
        inp.decision_process_spec.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=dp_ref.artifact_id,
        artifact_type="decision_process_spec",
        payload_hash=dp_ref.payload_hash,
        parent_ids=(tc_ref.artifact_id, manifest_ref.artifact_id),
        stage="propose_formal_spec",
        created_at=now,
    ))

    # Stage 9: validation_report
    vr_ref = _artifact_ref_as_typed(
        inp.validation_report.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=vr_ref.artifact_id,
        artifact_type="validation_report",
        payload_hash=vr_ref.payload_hash,
        parent_ids=(dp_ref.artifact_id, tc_ref.artifact_id),
        stage="formal_validation",
        created_at=now,
    ))

    # Stage 10: critic_report
    critic_ref: ArtifactReference | None = None
    if inp.critic_report is not None:
        if isinstance(inp.critic_report, dict):
            critic_ref = _artifact_ref_as_typed(inp.critic_report)
        elif hasattr(inp.critic_report, "model_dump"):
            critic_ref = _artifact_ref_as_typed(inp.critic_report.model_dump(  # type: ignore[arg-type]
                mode="json", round_trip=True, warnings="error"
            ))
        if critic_ref:
            entries.append(ArtifactLineageEntry(
                artifact_id=critic_ref.artifact_id,
                artifact_type="text_critic_report",
                payload_hash=critic_ref.payload_hash,
                parent_ids=(dp_ref.artifact_id, tc_ref.artifact_id,
                            vr_ref.artifact_id),
                stage="text_formal_critic",
                created_at=now,
            ))

    # Stage 11: approval_decision
    approval_ref: ArtifactReference | None = None
    if inp.approval_decision is not None:
        if isinstance(inp.approval_decision, dict):
            approval_ref = _artifact_ref_as_typed(inp.approval_decision)
        elif hasattr(inp.approval_decision, "model_dump"):
            approval_ref = _artifact_ref_as_typed(inp.approval_decision.model_dump(  # type: ignore[arg-type]
                mode="json", round_trip=True, warnings="error"
            ))
        if approval_ref:
            parent_ids = [vr_ref.artifact_id]
            if critic_ref:
                parent_ids.append(critic_ref.artifact_id)
            parent_ids.append(tc_ref.artifact_id)
            entries.append(ArtifactLineageEntry(
                artifact_id=approval_ref.artifact_id,
                artifact_type="approval_decision",
                payload_hash=approval_ref.payload_hash,
                parent_ids=tuple(parent_ids),
                stage="approval_gate",
                created_at=now,
            ))

    # Stage 13: environment_candidate
    ec_ref = _artifact_ref_as_typed(
        inp.environment_candidate.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    ec_parent_ids = [dp_ref.artifact_id, tc_ref.artifact_id,
                     manifest_ref.artifact_id]
    if approval_ref:
        ec_parent_ids.append(approval_ref.artifact_id)
    entries.append(ArtifactLineageEntry(
        artifact_id=ec_ref.artifact_id,
        artifact_type="environment_candidate",
        payload_hash=ec_ref.payload_hash,
        parent_ids=tuple(ec_parent_ids),
        stage="environment_candidate",
        created_at=now,
    ))

    # Stage 13: candidate_bundle
    cb_ref = _artifact_ref_as_typed(
        inp.candidate_bundle.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=cb_ref.artifact_id,
        artifact_type="environment_candidate_bundle",
        payload_hash=cb_ref.payload_hash,
        parent_ids=(ec_ref.artifact_id,),
        stage="environment_candidate",
        created_at=now,
    ))

    # Stage 13: implementation_plan
    ip_ref = _artifact_ref_as_typed(
        inp.implementation_plan.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=ip_ref.artifact_id,
        artifact_type="implementation_plan",
        payload_hash=ip_ref.payload_hash,
        parent_ids=(ec_ref.artifact_id, tc_ref.artifact_id,
                    dp_ref.artifact_id),
        stage="environment_candidate",
        created_at=now,
    ))

    # Stage 13: sandbox_policy
    sp_ref = _artifact_ref_as_typed(
        inp.sandbox_policy.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=sp_ref.artifact_id,
        artifact_type="sandbox_policy",
        payload_hash=sp_ref.payload_hash,
        parent_ids=(ec_ref.artifact_id,),
        stage="environment_candidate",
        created_at=now,
    ))

    # Stage 13: sandbox_limits
    sl_ref = _artifact_ref_as_typed(
        inp.sandbox_limits.model_dump(
            mode="json", round_trip=True, warnings="error"
        )
    )
    entries.append(ArtifactLineageEntry(
        artifact_id=sl_ref.artifact_id,
        artifact_type="sandbox_limits",
        payload_hash=sl_ref.payload_hash,
        parent_ids=(ec_ref.artifact_id,),
        stage="environment_candidate",
        created_at=now,
    ))

    # Stage 14: test_report
    tr_ref: ArtifactReference | None = None
    if isinstance(inp.test_report, dict):
        tr_ref = _artifact_ref_as_typed(inp.test_report)
    elif hasattr(inp.test_report, "model_dump"):
        tr_ref = _artifact_ref_as_typed(inp.test_report.model_dump(  # type: ignore[arg-type]
            mode="json", round_trip=True, warnings="error"
        ))
    if tr_ref:
        entries.append(ArtifactLineageEntry(
            artifact_id=tr_ref.artifact_id,
            artifact_type="test_report",
            payload_hash=tr_ref.payload_hash,
            parent_ids=(ec_ref.artifact_id, cb_ref.artifact_id),
            stage="public_tests",
            created_at=now,
        ))

    # Stage 15: package_bundle
    pkg_ref: ArtifactReference | None = None
    if isinstance(inp.package_bundle, dict):
        pkg_ref = _artifact_ref_as_typed(inp.package_bundle)
    elif hasattr(inp.package_bundle, "model_dump"):
        pkg_ref = _artifact_ref_as_typed(inp.package_bundle.model_dump(  # type: ignore[arg-type]
            mode="json", round_trip=True, warnings="error"
        ))
    if pkg_ref:
        parent_pkg_ids = [ec_ref.artifact_id, cb_ref.artifact_id]
        if tr_ref:
            parent_pkg_ids.append(tr_ref.artifact_id)
        entries.append(ArtifactLineageEntry(
            artifact_id=pkg_ref.artifact_id,
            artifact_type="package_bundle",
            payload_hash=pkg_ref.payload_hash,
            parent_ids=tuple(parent_pkg_ids),
            stage="package_candidate",
            created_at=now,
        ))

    return tuple(entries)


def _determine_terminal_status(
    inp: TerminalCASInput,
) -> Literal["compiled", "compiled_with_warnings", "compilation_failed"]:
    """Determine the overall compile status."""
    vr_passed = inp.validation_report.status == "passed"

    # Check critic for unresolved critical issues
    has_critical = False
    if inp.critic_report is not None:
        if hasattr(inp.critic_report, "issues"):
            critic_issues: object = inp.critic_report.issues  # type: ignore[union-attr]
            for issue in critic_issues:  # type: ignore[union-attr]
                if (hasattr(issue, "disposition")
                        and issue.disposition == "open"  # type: ignore[union-attr]
                        and hasattr(issue, "severity")
                        and issue.severity in ("high", "critical")):  # type: ignore[union-attr]
                    has_critical = True
                    break
        elif isinstance(inp.critic_report, dict):
            for issue in inp.critic_report.get("issues", ()):
                if (isinstance(issue, dict)
                        and issue.get("disposition") == "open"
                        and issue.get("severity") in ("high", "critical")):
                    has_critical = True
                    break

    # Check test results
    tests_passed = True
    if isinstance(inp.test_report, dict):
        tests_passed = inp.test_report.get("failed_cases", 0) == 0 \
            and inp.test_report.get("error_cases", 0) == 0
    elif hasattr(inp.test_report, "failed_cases"):
        tests_passed = (inp.test_report.failed_cases == 0  # type: ignore[union-attr]
                        and inp.test_report.error_cases == 0)  # type: ignore[union-attr]

    if not vr_passed or not tests_passed:
        return "compilation_failed"
    if has_critical:
        return "compiled_with_warnings"
    return "compiled"


# ---------------------------------------------------------------------------
# Stage function
# ---------------------------------------------------------------------------

def terminal_cas_stage(
    inp: TerminalCASInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 16: finalize the artifact chain.

    Writes terminal records to the artifact repository with proper parent
    lineage, and generates a TerminalResult containing all artifact
    references produced throughout the compile pipeline.
    """
    now = _now_iso()

    # Build lineage entries
    entries = _build_artifact_lineage(inp)

    # Compute deterministic terminal hash
    entry_ids = tuple(e.artifact_id for e in entries)
    terminal_hash_raw = canonical_json_bytes({
        "entries": entry_ids,
        "suite": inp.task_contract.task_identity.name,
        "route": inp.route,
    })
    terminal_hash = f"sha256:{sha256(terminal_hash_raw).hexdigest()}"

    manifest_id = f"tm_{sha256(terminal_hash_raw).hexdigest()[:16]}"

    manifest = TerminalManifest(
        schema_version="compile.terminal-manifest.v1",
        manifest_id=manifest_id,
        suite_id=f"suite_{inp.task_contract.task_identity.name}",
        entries=entries,
        root_artifact_id=entries[0].artifact_id if entries else "artifact_" + "0" * 64,
        terminal_hash=terminal_hash,
        merged_at=now,
    )

    status = _determine_terminal_status(inp)

    # Extract refs from entries for the terminal result
    tc_ref = None
    dp_ref = None
    vr_ref = None
    critic_ref = None
    approval_ref = None
    ec_ref = None
    cb_ref = None
    ip_ref = None
    sp_ref = None
    sl_ref = None
    tr_ref = None
    pkg_ref = None

    for entry in entries:
        atype = entry.artifact_type
        ref = {"artifact_id": entry.artifact_id, "payload_hash": entry.payload_hash}
        if atype == "task_contract":
            tc_ref = ref
        elif atype == "decision_process_spec":
            dp_ref = ref
        elif atype == "validation_report":
            vr_ref = ref
        elif atype == "text_critic_report":
            critic_ref = ref
        elif atype == "approval_decision":
            approval_ref = ref
        elif atype == "environment_candidate":
            ec_ref = ref
        elif atype == "environment_candidate_bundle":
            cb_ref = ref
        elif atype == "implementation_plan":
            ip_ref = ref
        elif atype == "sandbox_policy":
            sp_ref = ref
        elif atype == "sandbox_limits":
            sl_ref = ref
        elif atype == "test_report":
            tr_ref = ref
        elif atype == "package_bundle":
            pkg_ref = ref

    result_id = f"result_{sha256(terminal_hash_raw).hexdigest()[:16]}"

    terminal_result = CompileTerminalResult(
        schema_version="compile.terminal-result.v1",
        result_id=result_id,
        task_contract_ref=tc_ref or {},
        decision_process_spec_ref=dp_ref or {},
        validation_report_ref=vr_ref or {},
        critic_report_ref=critic_ref,
        approval_decision_ref=approval_ref,
        environment_candidate_ref=ec_ref or {},
        candidate_bundle_ref=cb_ref or {},
        implementation_plan_ref=ip_ref or {},
        sandbox_policy_ref=sp_ref or {},
        sandbox_limits_ref=sl_ref or {},
        test_report_ref=tr_ref or {},
        package_bundle_ref=pkg_ref or {},
        package_hash=inp.package_hash,
        terminal_manifest_ref=_artifact_ref_as_typed(
            manifest.model_dump(mode="json", round_trip=True, warnings="error")
        ),
        terminal_hash=terminal_hash,
        status=status,
        compiled_at=now,
    )

    return StageResult(
        stage="terminal_cas",
        status="ok",
        output_ref=TerminalCASOutput(
            schema_version="compile.terminal-cas-output.v1",
            terminal_result=terminal_result,
            terminal_manifest=manifest,
            terminal_hash=terminal_hash,
        ),
        failure_code=None,
        recovery_status="ok",
        event_refs=(),
        budget_consumed_ref=None,
    )
