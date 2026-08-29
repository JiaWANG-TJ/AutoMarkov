"""Stage 5: parse evidence into structured claims, build claim-evidence edges, validate confidence."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, cast

from automarkov.application._common import StageResult, _artifact_ref, _canonical_safe
from automarkov.domain.models import StrictFrozenModel


class ClaimEvidenceGraphInput(StrictFrozenModel):
    schema_version: Literal["compile.claim-evidence-input.v1"]
    evidence_snapshot_ref: object
    manifest_ref: object
    grant_ref: object


class ClaimEvidenceGraphOutput(StrictFrozenModel):
    schema_version: Literal["compile.claim-evidence-output.v1"]
    generation_view: object


def _parse_claims_from_snippets(sources: tuple[dict[str, str], ...]) -> tuple[dict[str, object], ...]:
    """Parse evidence snippets into structured claims deterministically."""
    claims: list[dict[str, object]] = []
    for idx, src in enumerate(sources):
        snippet = src.get("snippet", "")
        lowered = snippet.lower()
        claim_id = f"C-{sha256(snippet.encode()).hexdigest()[:12]}"

        # Heuristic claim extraction
        confidence = 0.5  # base confidence for web snippets
        claim_types: list[str] = []
        if any(kw in lowered for kw in ("state", "observation", "variable")):
            claim_types.append("state_variables")
            confidence = max(confidence, 0.6)
        if any(kw in lowered for kw in ("action", "control", "intervention")):
            claim_types.append("action_space")
            confidence = max(confidence, 0.6)
        if any(kw in lowered for kw in ("reward", "objective", "cost", "utility")):
            claim_types.append("reward_function")
            confidence = max(confidence, 0.6)
        if any(kw in lowered for kw in ("transition", "dynamics", "kinematic")):
            claim_types.append("transition_dynamics")
            confidence = max(confidence, 0.5)
        if any(kw in lowered for kw in ("multi-agent", "decentralized", "cooperative", "competitive")):
            claim_types.append("agent_structure")
            confidence = max(confidence, 0.6)
        if any(kw in lowered for kw in ("partially observable", "partial observ", "pomdp", "hidden")):
            claim_types.append("observability")
            confidence = max(confidence, 0.7)
        if any(kw in lowered for kw in ("markov", "memoryless")):
            claim_types.append("markov_property")
            confidence = max(confidence, 0.7)
        if any(kw in lowered for kw in ("continuous", "discrete", "finite")):
            claim_types.append("domain_type")
            confidence = max(confidence, 0.5)
        if not claim_types:
            claim_types.append("general_context")
            confidence = 0.4

        claims.append({
            "claim_id": claim_id,
            "source_url": src.get("url", ""),
            "source_title": src.get("title", ""),
            "statement": snippet[:500],
            "claim_types": tuple(sorted(set(claim_types))),
            "confidence": min(confidence, 0.9),
            "validated": False,
            "evidence_ids": (f"E-{sha256(src.get('url', '').encode()).hexdigest()[:16]}",),
        })
    return tuple(claims)


def _validate_confidence_scores(claims: tuple[dict[str, object], ...]) -> bool:
    """Validate that all confidence scores are in (0, 1)."""
    for c in claims:
        conf = c.get("confidence", 0.0)
        if isinstance(conf, float) and not (0.0 <= conf <= 1.0):
            return False
    return True


def _build_evidence_edges(claims: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    """Build claim-evidence adjacency edges."""
    edges: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for c in claims:
        claim_id = cast(str, c["claim_id"])
        for ev_id in cast(tuple[str, ...], c.get("evidence_ids", ())):
            key = (claim_id, cast(str, ev_id))
            if key not in seen:
                seen.add(key)
                edges.append({"from_claim": claim_id, "to_evidence": cast(str, ev_id), "relation": "supports"})
    return tuple(edges)


def claim_evidence_graph_stage(
    inp: ClaimEvidenceGraphInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 5: parse evidence into structured claims, build edges, validate confidence scores."""
    snap = inp.evidence_snapshot_ref if isinstance(inp.evidence_snapshot_ref, dict) else {}
    sources = snap.get("sources", ())
    claims = _parse_claims_from_snippets(sources)
    edges = _build_evidence_edges(claims)
    confidence_valid = _validate_confidence_scores(claims)

    if not confidence_valid:
        return StageResult(
            stage="claim_evidence_graph", status="failed",
            output_ref=None,
            failure_code="evidence_claim_failed",
            recovery_status="ok",
            event_refs=(), budget_consumed_ref=None,
        )

    # Build the real GenerationEvidenceView with proper typed stores
    sid = f"store_{sha256(_canonical_safe(snap if snap else {'stub': True})).hexdigest()[:16]}"
    identity_hash = f"sha256:{sha256(sid.encode()).hexdigest()}"

    from automarkov.domain.models import EvidenceCapabilityGrant, EvidenceStoreRef, GenerationEvidenceView
    store_ref = EvidenceStoreRef.model_validate({
        "schema_version": "automarkov.evidence-store-ref.v1",
        "store_id": sid, "tier": "allowed_evidence", "identity_hash": identity_hash,
    }, strict=True)
    grant = EvidenceCapabilityGrant.model_validate({
        "schema_version": "automarkov.evidence-capability-grant.v1",
        "signing_domain": "AutoMarkov-Evidence-Capability-Grant-v1",
        "capability_id": "capability_compile", "principal_id": "principal_compiler",
        "principal_kind": "researcher", "tiers": ("allowed_evidence",),
        "store_ids": (sid,), "store_identity_hashes": {sid: identity_hash},
        "issuer_key_id": "key_compiler", "nonce": "A" * 43,
        "signature_algorithm": "Ed25519", "signature": "A" * 86,
    }, strict=True)
    generation_view_model = GenerationEvidenceView.model_validate({
        "schema_version": "automarkov.generation-evidence-view.v1",
        "principal_id": "principal_compiler",
        "capability_grant": grant, "stores": (store_ref,),
    }, strict=True)

    view: dict[str, object] = {
        "schema_version": "automarkov.claim-evidence-graph.v1",
        "claims": claims,
        "edges": edges,
        "claim_metadata": {
            "total_claims": len(claims),
            "total_edges": len(edges),
            "confidence_valid": confidence_valid,
            "source_count": len(sources),
        },
        "generation_evidence_view": generation_view_model.model_dump(mode="json", round_trip=True, warnings="error"),
    }

    return StageResult(
        stage="claim_evidence_graph", status="ok",
        output_ref=ClaimEvidenceGraphOutput(
            schema_version="compile.claim-evidence-output.v1",
            generation_view=_artifact_ref(view),
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
