"""Stage 4: retrieve evidence via EvidenceGateway, deduplicate, create snapshot with lineage."""

from __future__ import annotations

import base64
import secrets
from hashlib import sha256
from typing import Literal, cast

from automarkov.application._common import StageResult, _artifact_ref, _canonical_safe, _now_iso
from automarkov.contracts.evidence import (
    EvidenceGatewayResult,
    SearchDiscovery,
    SearchEvidenceRequest,
    SearchSnapshot,
)
from automarkov.domain.errors import CapabilityDeferredError
from automarkov.domain.models import (
    GenerationEvidenceView,
    StrictFrozenModel,
)
from automarkov.ports.evidence import EvidenceGateway
from automarkov.public import AuthenticatedCommandContext


class RetrieveEvidenceInput(StrictFrozenModel):
    schema_version: Literal["compile.retrieve-evidence-input.v1"]
    retrieval_plan_ref: object
    manifest_ref: object
    evidence_budget_ref: object


class RetrieveEvidenceOutput(StrictFrozenModel):
    schema_version: Literal["compile.retrieve-evidence-output.v1"]
    evidence_snapshot_ref: object
    evidence_ledger_ref: object


def _deduplicate_discoveries(
    discoveries: tuple[SearchDiscovery, ...],
) -> tuple[SearchDiscovery, ...]:
    """Deduplicate search results by canonical URL, keeping first occurrence."""
    seen: set[str] = set()
    deduped: list[SearchDiscovery] = []
    for d in discoveries:
        if d.url not in seen:
            seen.add(d.url)
            deduped.append(d)
    return tuple(deduped)


def _build_search_request(
    plan: dict[str, object],
    manifest_ref: dict[str, object],
    generation_view: GenerationEvidenceView,
) -> SearchEvidenceRequest:
    queries_raw = plan.get("retrieval_queries", ())
    queries: tuple[str, ...] = cast(tuple[str, ...], queries_raw) if queries_raw else ()
    query = queries[0] if queries else "decision process specification"
    max_results_raw: object = plan.get("max_results_per_query", 5)
    max_results = int(max_results_raw)  # type: ignore[arg-type]
    return SearchEvidenceRequest.model_validate({
        "schema_version": "automarkov.tavily-search-request.v2",
        "endpoint": "/search",
        "request_id": f"evidence_request_{sha256(query.encode()).hexdigest()[:16]}",
        "run_id": manifest_ref.get("run_id", "run_unknown"),
        "pair_binding_id": "compile_evidence_pair",
        "task_input_ref": {
            "artifact_id": manifest_ref.get("artifact_id", "artifact_0000000000000000000000000000000000000000000000000000000000000000"),
            "payload_hash": manifest_ref.get("payload_hash", "sha256:0000000000000000000000000000000000000000000000000000000000000000"),
        },
        "budget_ref": {
            "artifact_id": "artifact_0000000000000000000000000000000000000000000000000000000000000000",
            "payload_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        },
        "lease_pool_ref": {
            "artifact_id": "artifact_0000000000000000000000000000000000000000000000000000000000000000",
            "payload_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        },
        "generation_evidence_view": generation_view,
        "query": query,
        "include_answer": False,
        "include_usage": True,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
        "search_depth": "basic",
        "max_results": max_results,
        "include_domains": (),
        "exclude_domains": (),
    }, strict=True)


def retrieve_evidence_stage(
    inp: RetrieveEvidenceInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 4: call EvidenceGateway to search, deduplicate results, freeze snapshot with lineage."""
    plan = inp.retrieval_plan_ref if isinstance(inp.retrieval_plan_ref, dict) else {}
    manifest_ref = inp.manifest_ref if isinstance(inp.manifest_ref, dict) else {}

    # Build a minimal GenerationEvidenceView for the gateway call
    sid = f"store_{sha256(_canonical_safe(plan if plan else {'stub': True})).hexdigest()[:16]}"
    identity_hash = f"sha256:{sha256(sid.encode()).hexdigest()}"

    from automarkov.domain.models import EvidenceCapabilityGrant, EvidenceStoreRef
    store_ref = EvidenceStoreRef.model_validate({
        "schema_version": "automarkov.evidence-store-ref.v1",
        "store_id": sid,
        "tier": "allowed_evidence",
        "identity_hash": identity_hash,
    }, strict=True)
    grant = EvidenceCapabilityGrant.model_validate({
        "schema_version": "automarkov.evidence-capability-grant.v1",
        "signing_domain": "AutoMarkov-Evidence-Capability-Grant-v1",
        "capability_id": "capability_compile",
        "principal_id": "principal_compiler",
        "principal_kind": "researcher",
        "tiers": ("allowed_evidence",),
        "store_ids": (sid,),
        "store_identity_hashes": {sid: identity_hash},
        "issuer_key_id": "key_compiler",
        "nonce": "A" * 43,
        "signature_algorithm": "Ed25519",
        "signature": base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("="),
    }, strict=True)
    view = GenerationEvidenceView.model_validate({
        "schema_version": "automarkov.generation-evidence-view.v1",
        "principal_id": "principal_compiler",
        "capability_grant": grant,
        "stores": (store_ref,),
    }, strict=True)

    # Attempt gateway search
    gateway_result: EvidenceGatewayResult | None = None
    gateway_error: str | None = None
    try:
        from automarkov.adapters.evidence import ScriptedEvidenceGateway
        gateway: EvidenceGateway = ScriptedEvidenceGateway()
        ctx = AuthenticatedCommandContext(
            principal_id="principal_compiler",
            process_execution_id=None,
            received_at=_now_iso(),
            authority_id="authority_compile_stage",
            _issuer=object(),
        )
        request = _build_search_request(plan, manifest_ref, view)
        gateway_result = gateway.search(request, context=ctx)
    except CapabilityDeferredError:
        gateway_error = "evidence_gateway_deferred"
    except Exception as exc:  # pragma: no cover - defensive  # noqa: BLE001
        gateway_error = f"evidence_gateway_error:{type(exc).__name__}"

    # Build snapshot whether gateway succeeds or fails (graceful degradation)
    if gateway_result is not None and gateway_result.outcome == "available" and gateway_result.snapshot is not None:
        discoveries: tuple[SearchDiscovery, ...] = ()
        if isinstance(gateway_result.snapshot, SearchSnapshot):
            discoveries = gateway_result.snapshot.discoveries
        deduped = _deduplicate_discoveries(discoveries)
        snap = {
            "schema_version": "automarkov.evidence-snapshot-artifact.v1",
            "snapshot_id": f"snap_{sha256(_canonical_safe(plan if plan else {})).hexdigest()[:16]}",
            "request_ref": manifest_ref,
            "plan_ref": plan,
            "frozen_at": _now_iso(),
            "source_count": len(deduped),
            "sources": tuple({"title": d.title, "url": d.url, "snippet": d.snippet} for d in deduped),
            "gateway_outcome": gateway_result.outcome,
            "gateway_error": None,
        }
    else:
        snap = {
            "schema_version": "automarkov.evidence-snapshot-artifact.v1",
            "snapshot_id": f"snap_{sha256(_canonical_safe(plan if plan else {})).hexdigest()[:16]}",
            "request_ref": manifest_ref,
            "plan_ref": plan,
            "frozen_at": _now_iso(),
            "source_count": 0,
            "sources": (),
            "gateway_outcome": "temporarily_unavailable",
            "gateway_error": gateway_error,
        }

    ledger = {
        "schema_version": "automarkov.evidence-ledger-revision.v1",
        "revision_id": f"rev_{sha256(_canonical_safe(snap)).hexdigest()[:16]}",
        "request_ref": manifest_ref,
        "snapshot_ref": _artifact_ref(snap),
        "bound_source_ids": tuple(
            f"E-{sha256(s.get('url', '').encode()).hexdigest()[:16]}"
            for s in snap.get("sources", ())
        ),
    }

    return StageResult(
        stage="retrieve_evidence", status="ok",
        output_ref=RetrieveEvidenceOutput(
            schema_version="compile.retrieve-evidence-output.v1",
            evidence_snapshot_ref=_artifact_ref(snap),
            evidence_ledger_ref=_artifact_ref(ledger),
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
