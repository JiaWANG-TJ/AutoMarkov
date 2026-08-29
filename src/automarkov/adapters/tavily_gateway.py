"""Production Tavily evidence gateway adapter.

Environment variables:
    TAVILY_API_KEY_01 through TAVILY_API_KEY_29 — API keys for the Tavily evidence
    provider. Each key is a distinct credential slot provisioned under a separate
    Tavily account or billing sub-account. Keys are never logged, stored in
    artifacts, or exposed in error messages/results.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from automarkov.contracts.evidence import (
    TAVILY_SLOT_IDS,
    CrawlEvidenceRequest,
    CrawlSnapshot,
    EvidenceGatewayResult,
    ExtractEvidenceRequest,
    ExtractSnapshot,
    ProviderAttemptReceipt,
    RawEvidenceDocument,
    SearchDiscovery,
    SearchEvidenceRequest,
    SearchSnapshot,
)
from automarkov.domain.errors import EvidenceProviderContractError
from automarkov.public import AuthenticatedCommandContext

_ORIGIN = "https://api.tavily.com"
_TIMEOUT = 60.0


@dataclass
class _Slot:
    slot_id: str
    _key: str = field(repr=False)
    leased_until: float = 0.0
    win_start: float = 0.0
    win_count: int = 0


def _sha(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode()).hexdigest()}"


def _doc(content: str, url: str) -> RawEvidenceDocument:
    return RawEvidenceDocument(
        schema_version="automarkov.raw-evidence-document.v1",
        source_url=url, content=content, content_hash=_sha(content),
        store_tier="allowed_evidence",
    )


def _receipt(slot_id: str, endpoint: str, attempt: int, status: int | None, dur_ms: int) -> ProviderAttemptReceipt:
    return ProviderAttemptReceipt(
        schema_version="automarkov.provider-attempt-receipt.v1", slot_id=slot_id,
        endpoint=endpoint,  # type: ignore[arg-type]
        attempt_number=attempt, http_status=status,
        provider_request_id=None, request_hash=_sha(""), response_hash=None,
        duration_ms=dur_ms, usage_credits=None, credit_reservation=1.0, cost_state="ambiguous",
    )


def _result(
    request_id: str, outcome: str, snapshot: SearchSnapshot | ExtractSnapshot | CrawlSnapshot | None,
    receipts: list[ProviderAttemptReceipt], reason: str | None = None,
) -> EvidenceGatewayResult:
    return EvidenceGatewayResult(
        schema_version="automarkov.evidence-gateway-result.v1", outcome=outcome,  # type: ignore[arg-type]
        request_id=request_id, snapshot=snapshot, attempt_receipts=tuple(receipts),
        request_ref=None, attempt_receipt_refs=(), raw_document_refs=(),
        snapshot_ref=None, ledger_revision_ref=None, earliest_availability=None, reason_code=reason,
    )


class ProductionTavilyGateway:
    """Production Tavily evidence gateway with round-robin key rotation.

    Implements the EvidenceGateway protocol for Tavily Search, Extract, and Crawl.
    Manages up to 29 API keys with per-key cooldown and sliding-window rate limits.
    Keys are never exposed in logs, artifacts, or error payloads.
    """

    def __init__(self, *, keys: dict[str, str] | None = None,
                 non_crawl_rpm: int = 20, crawl_rpm: int = 5, cooldown_s: float = 1.0) -> None:
        resolved = keys or {sid: v for sid in TAVILY_SLOT_IDS if (v := os.environ.get(sid, ""))}
        if not resolved or any(type(k) is not str or type(v) is not str or not v for k, v in resolved.items()):
            raise EvidenceProviderContractError("invalid_key_map")
        self._slots = [_Slot(sid, _key=v) for sid, v in resolved.items()]
        self._rpm = {False: non_crawl_rpm, True: crawl_rpm}
        self._cooldown = cooldown_s
        self._cursor = 0
        self._lock = threading.Lock()

    # -- EvidenceGateway protocol ----------------------------------------

    def search(self, request: SearchEvidenceRequest, *, context: AuthenticatedCommandContext) -> EvidenceGatewayResult:
        return self._dispatch(request, context, "/search",
            {"query": request.query, "include_answer": False, "include_usage": True,
             "include_raw_content": False, "include_images": False, "auto_parameters": False,
             "search_depth": request.search_depth, "max_results": request.max_results,
             "include_domains": list(request.include_domains), "exclude_domains": list(request.exclude_domains)},
            lambda resp: SearchSnapshot(schema_version="automarkov.search-snapshot.v1",
                snapshot_kind="discovery_only", request_id=request.request_id, discoveries=tuple(
                    SearchDiscovery(title=str(d.get("title","")), url=str(d.get("url","")),
                                    snippet=str(d.get("content","")))
                    for d in self._results(resp))))

    def extract(self, request: ExtractEvidenceRequest, *, context: AuthenticatedCommandContext) -> EvidenceGatewayResult:
        return self._dispatch(request, context, "/extract",
            {"urls": list(request.urls), "include_usage": True, "include_images": False,
             "extract_depth": request.extract_depth, "format": request.format,
             "timeout": request.timeout_seconds},
            lambda resp: self._extract_snap(request, resp))

    def crawl(self, request: CrawlEvidenceRequest, *, context: AuthenticatedCommandContext) -> EvidenceGatewayResult:
        self._chk_ctx(context)
        receipts: list[ProviderAttemptReceipt] = []
        payload = {"url": request.root_url, "allow_external": False, "include_usage": True,
                   "include_images": False, "max_depth": request.max_depth,
                   "max_breadth": request.max_breadth, "limit": request.limit,
                   "timeout": request.timeout_seconds, "select_paths": list(request.select_paths),
                   "exclude_paths": list(request.exclude_paths)}
        for attempt in range(1, 3):
            s, k = self._acquire(is_crawl=True)
            resp, rct = self._send(s, k, "/crawl", payload, attempt)
            receipts.append(rct)
            if resp is not None and resp.status_code == 200:
                snap = CrawlSnapshot(schema_version="automarkov.crawl-snapshot.v1",
                    request_id=request.request_id, root_url=request.root_url,
                    documents=tuple(_doc(str(d.get("raw_content", d.get("content",""))),
                                         str(d.get("url",""))) for d in self._results(resp)))
                self._release(s, success=True)
                return _result(request.request_id, "available", snap, receipts)
            if resp is None or resp.status_code >= 500 or resp.status_code == 429:
                self._release(s, success=False); continue
            self._release(s, success=True); break
        return _result(request.request_id, "blocked", None, receipts, reason="crawl_exhausted")

    # -- dispatch --------------------------------------------------------

    def _dispatch(self, request: Any, context: AuthenticatedCommandContext, endpoint: str,
                  payload: dict[str, Any], build) -> EvidenceGatewayResult:
        self._chk_ctx(context)
        s, k = self._acquire(is_crawl=(endpoint == "/crawl"))
        resp, rct = self._send(s, k, endpoint, payload, 1)
        if resp is None:
            return _result(request.request_id, "blocked", None, [rct], reason="transport_error")
        self._release(s, success=resp.status_code == 200)
        if resp.status_code != 200:
            return _result(request.request_id, "blocked", None, [rct], reason=f"http_{resp.status_code}")
        try:
            return _result(request.request_id, "available", build(resp), [rct])
        except Exception:  # noqa: BLE001
            return _result(request.request_id, "blocked", None, [rct], reason="parse_error")

    # -- snapshot helpers ------------------------------------------------

    def _results(self, resp: httpx.Response) -> list[dict[str, Any]]:
        results = resp.json().get("results", [])
        if not isinstance(results, list):
            raise EvidenceProviderContractError("results_missing")
        return [r for r in results if isinstance(r, dict)]

    def _extract_snap(self, request: ExtractEvidenceRequest, resp: httpx.Response) -> ExtractSnapshot:
        body, failed = resp.json(), set()
        fra = body.get("failed_results", [])
        if isinstance(fra, list):
            for item in fra:
                if isinstance(item, str): failed.add(item)
                elif isinstance(item, dict) and isinstance(item.get("url"), str): failed.add(str(item["url"]))
        return ExtractSnapshot(schema_version="automarkov.extract-snapshot.v1",
            request_id=request.request_id,
            documents=tuple(_doc(str(d.get("raw_content", d.get("content",""))), str(d.get("url","")))
                            for d in self._results(resp)),
            failed_urls=tuple(sorted(failed)))

    # -- key pool --------------------------------------------------------

    def _acquire(self, *, is_crawl: bool) -> tuple[_Slot, str]:
        rpm = self._rpm[is_crawl]
        with self._lock:
            now = time.monotonic()
            for _ in range(len(self._slots)):
                self._cursor = (self._cursor + 1) % len(self._slots)
                s = self._slots[self._cursor]
                if s.leased_until > now: continue
                if now - s.win_start >= 60.0: s.win_start, s.win_count = now, 0
                if s.win_count >= rpm: continue
                s.leased_until = now + self._cooldown
                s.win_count += 1
                return s, s._key
        raise EvidenceProviderContractError("no_available_key_slot")

    def _release(self, slot: _Slot, *, success: bool) -> None:
        if not success:
            with self._lock:
                slot.leased_until = time.monotonic() + self._cooldown * 2

    # -- transport -------------------------------------------------------

    def _send(self, slot: _Slot, key: str, endpoint: str, payload: dict[str, Any], attempt: int):
        started = time.monotonic()
        try:
            resp = httpx.post(f"{_ORIGIN}{endpoint}",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload, timeout=float(payload.get("timeout", _TIMEOUT)), follow_redirects=False)
        except httpx.TransportError:
            resp = None
        finally:
            del key
        return resp, _receipt(slot.slot_id, endpoint, attempt,
                              resp.status_code if resp is not None else None,
                              max(0, int((time.monotonic() - started) * 1000)))

    @staticmethod
    def _chk_ctx(context: AuthenticatedCommandContext) -> None:
        if type(context) is not AuthenticatedCommandContext:
            raise EvidenceProviderContractError("invalid_context")
