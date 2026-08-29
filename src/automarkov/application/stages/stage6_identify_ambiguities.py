"""Stage 6: compare claims against task requirements, detect contradictions, classify gaps."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, cast

from automarkov.application._common import StageResult, _artifact_ref, _canonical_safe
from automarkov.domain.models import StrictFrozenModel


class IdentifyAmbiguitiesInput(StrictFrozenModel):
    schema_version: Literal["compile.identify-ambiguities-input.v1"]
    generation_view: object
    manifest_ref: object


class IdentifyAmbiguitiesOutput(StrictFrozenModel):
    schema_version: Literal["compile.identify-ambiguities-output.v1"]
    ambiguities_report_ref: object
    assumptions_report_ref: object


# Required claim categories for a complete task specification
_REQUIRED_CATEGORIES = frozenset({
    "state_variables", "action_space", "reward_function",
    "agent_structure", "transition_dynamics", "observability",
})

# Conflict detection: pairs of claim types that may indicate contradictions
_CONFLICT_PAIRS: tuple[tuple[tuple[str, str], str], ...] = (
    (("observability", "full_state_observability"),
     "observability markers conflict with full observability claim"),
    (("agent_structure", "single_agent"),
     "agent structure contradicts single-agent claim"),
    (("markov_property", "non_markovian"),
     "Markov property markers conflict with non-Markovian claim"),
    (("continuous_time", "discrete_time"),
     "continuous-time markers conflict with discrete-time description"),
)


def _extract_claim_categories(claims: tuple[dict[str, object], ...]) -> set[str]:
    """Collect all claim type categories from parsed claims."""
    categories: set[str] = set()
    for c in claims:
        for ct in cast(tuple[str, ...], c.get("claim_types", ())):
            categories.add(ct)
    return categories


def _detect_missing_categories(covered: set[str]) -> list[dict[str, object]]:
    """Detect required categories not covered by any claim."""
    issues: list[dict[str, object]] = []
    for req in sorted(_REQUIRED_CATEGORIES):
        if req not in covered:
            issues.append({
                "issue_id": f"amb_missing_{req}",
                "severity": "medium",
                "type": "ambiguity",
                "path": f"evidence.claims.{req}",
                "reason": f"Required category '{req}' is not covered by any evidence claim",
                "consequence": "Task specification may be incomplete",
                "question": f"What {req.replace('_', ' ')} does this task involve?",
                "evidence_ids": (),
                "disposition": "open",
                "accepted_assumption_id": None,
            })
    return issues


def _detect_contradictions(claims: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    """Detect contradictory claims between sources."""
    issues: list[dict[str, object]] = []
    claim_texts = {(cast(str, c.get("claim_id")), " ".join(cast(tuple[str, ...], c.get("claim_types", ())))) for c in claims}
    for (type_a, type_b), reason in _CONFLICT_PAIRS:
        has_a = any(type_a in text for _, text in claim_texts)
        has_b = any(type_b in text for _, text in claim_texts)
        if has_a and has_b:
            issues.append({
                "issue_id": f"amb_conflict_{type_a}_vs_{type_b}",
                "severity": "high",
                "type": "contradiction",
                "path": "evidence.claims",
                "reason": reason,
                "consequence": "Ambiguous specification may lead to incorrect model choice",
                "question": f"Does this task involve {type_a.replace('_', ' ')} or {type_b.replace('_', ' ')}?",
                "evidence_ids": (),
                "disposition": "open",
                "accepted_assumption_id": None,
            })
    return issues


def _classify_unresolved_gaps(issues: list[dict[str, object]]) -> tuple[
    list[dict[str, object]], list[dict[str, object]]
]:
    """Classify unresolved gaps as ambiguity issues or assumptions."""
    ambiguities: list[dict[str, object]] = []
    assumptions: list[dict[str, object]] = []
    for issue in issues:
        if issue.get("severity") in ("high", "critical"):
            amb = dict(issue)
            amb["disposition"] = "open"
            ambiguities.append(amb)
        else:
            assumption_id = f"asm_{sha256(_canonical_safe(issue)).hexdigest()[:12]}"
            assumptions.append({
                "assumption_id": assumption_id,
                "issue_ref": issue.get("issue_id", ""),
                "statement": issue.get("question", ""),
                "severity": issue.get("severity", "low"),
                "evidence_ids": issue.get("evidence_ids", ()),
                "category": issue.get("type", "ambiguity"),
                "path": issue.get("path", ""),
                "disposition": "converted_to_explicit_assumption",
            })
            issue["disposition"] = "converted_to_explicit_assumption"
            issue["accepted_assumption_id"] = assumption_id
    return ambiguities, assumptions


def identify_ambiguities_stage(
    inp: IdentifyAmbiguitiesInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 6: compare claims against requirements, detect contradictions, classify gaps."""
    gv = inp.generation_view if isinstance(inp.generation_view, dict) else {}
    claims = gv.get("claims", ())

    covered = _extract_claim_categories(claims)
    missing_issues = _detect_missing_categories(covered)
    contradiction_issues = _detect_contradictions(claims)
    all_issues = missing_issues + contradiction_issues

    ambiguity_issues, assumption_items = _classify_unresolved_gaps(all_issues)

    report = {
        "schema_version": "automarkov.ambiguities-report.v1",
        "issues": tuple(all_issues),
        "ambiguity_count": len(ambiguity_issues),
        "contradiction_count": len(contradiction_issues),
        "covered_categories": tuple(sorted(covered)),
        "missing_categories": tuple(sorted(_REQUIRED_CATEGORIES - covered)),
        "total_claims_analyzed": len(claims),
    }

    assumptions_report = {
        "schema_version": "automarkov.assumptions-report.v1",
        "assumptions": tuple(assumption_items),
        "total_assumptions": len(assumption_items),
    }

    return StageResult(
        stage="identify_ambiguities", status="ok",
        output_ref=IdentifyAmbiguitiesOutput(
            schema_version="compile.identify-ambiguities-output.v1",
            ambiguities_report_ref=_artifact_ref(report),
            assumptions_report_ref=_artifact_ref(assumptions_report),
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
