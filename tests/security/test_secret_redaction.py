from __future__ import annotations

from dataclasses import replace

import pytest

from automarkov.contracts.evidence import EvidenceBudgetManifest, SearchEvidenceRequest
from automarkov.public import CommandAuthority, CommandPrincipalBinding
from automarkov.tavily_gateway import (
    EvidenceGatewayAuthenticationError,
    SecretRef,
    SqliteTavilyKeyLeaseStore,
    TavilyEvidenceGateway,
    TavilyTransportResponse,
)


def _budget() -> EvidenceBudgetManifest:
    return EvidenceBudgetManifest.model_validate(
        {
            "schema_version": "automarkov.evidence-budget-manifest.v1",
            "pair_binding_id": "pair_001",
            "provider": "tavily",
            "api_origin": "https://api.tavily.com",
            "endpoints": ["/crawl", "/extract", "/search"],
            "credit_ceiling": 100,
            "logical_call_ceiling": 20,
            "attempt_ceiling_per_call": 4,
            "key_lease_wait_seconds": 3.5,
            "non_crawl_requests_per_minute": 30,
            "crawl_requests_per_minute": 10,
            "search_max_results": 5,
            "extract_max_urls": 10,
            "crawl_max_depth": 2,
            "crawl_max_breadth": 10,
            "crawl_page_limit": 20,
            "retry_base_seconds": 0.25,
            "retry_max_seconds": 4.5,
            "full_jitter": True,
            "allowed_domains": ["docs.example.org"],
            "blocked_domains": [],
            "allowed_url_prefixes": ["https://docs.example.org/"],
            "blocked_path_prefixes": ["/private"],
        },
        strict=True,
    )


def _search_request() -> SearchEvidenceRequest:
    return SearchEvidenceRequest.model_validate(
        {
            "schema_version": "automarkov.tavily-search-request.v2",
            "request_id": "evidence_request_001",
            "run_id": "run_001",
            "pair_binding_id": "pair_001",
            "task_input_ref": {
                "artifact_id": "artifact_" + "1" * 64,
                "payload_hash": "sha256:" + "1" * 64,
            },
            "budget_ref": {
                "artifact_id": "artifact_" + "2" * 64,
                "payload_hash": "sha256:" + "2" * 64,
            },
            "lease_pool_ref": {
                "artifact_id": "artifact_" + "3" * 64,
                "payload_hash": "sha256:" + "3" * 64,
            },
            "generation_evidence_view": {
                "schema_version": "automarkov.generation-evidence-view.v1",
                "principal_id": "principal_researcher",
                "capability_grant": {
                    "schema_version": "automarkov.evidence-capability-grant.v1",
                    "signing_domain": "AutoMarkov-Evidence-Capability-Grant-v1",
                    "capability_id": "capability_evidence",
                    "principal_id": "principal_researcher",
                    "principal_kind": "researcher",
                    "tiers": ["allowed_evidence"],
                    "store_ids": ["store_allowed"],
                    "store_identity_hashes": {
                        "store_allowed": "sha256:" + "4" * 64,
                    },
                    "issuer_key_id": "key_evidence",
                    "nonce": "A" * 43,
                    "signature_algorithm": "Ed25519",
                    "signature": "A" * 86,
                },
                "stores": [
                    {
                        "schema_version": "automarkov.evidence-store-ref.v1",
                        "store_id": "store_allowed",
                        "tier": "allowed_evidence",
                        "identity_hash": "sha256:" + "4" * 64,
                    }
                ],
            },
            "endpoint": "/search",
            "query": "official contract",
            "include_answer": False,
            "include_usage": True,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
            "search_depth": "basic",
            "max_results": 5,
            "include_domains": ["docs.example.org"],
            "exclude_domains": [],
        },
        strict=True,
    )


class RecordingSecrets:
    def __init__(self) -> None:
        self.refs: list[SecretRef] = []

    def resolve(self, ref: SecretRef) -> str:
        self.refs.append(ref)
        return "never-print-this-secret"


class RecordingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def send(
        self, *, origin, endpoint, payload, request_id, secret_ref, secret_provider
    ):
        self.calls += 1
        secret_provider.resolve(secret_ref)
        return TavilyTransportResponse(
            status_code=200,
            headers={},
            body=(
                b'{"request_id":"provider-1","answer":null,'
                b'"results":[],"usage":{"credits":1.0}}'
            ),
        )


def test_authentication_fails_before_secret_resolution_or_network(tmp_path) -> None:
    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    valid_context = authority.issue(
        "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
    )
    forged_context = replace(valid_context, _issuer=object())
    secrets = RecordingSecrets()
    transport = RecordingTransport()
    gateway = TavilyEvidenceGateway(
        budget=_budget(),
        lease_store=SqliteTavilyKeyLeaseStore(
            tmp_path / "leases.sqlite3", server_secret=b"s" * 32
        ),
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=transport,
        secret_provider=secrets,
        clock=lambda: 100.0,
    )

    with pytest.raises(EvidenceGatewayAuthenticationError) as failure:
        gateway.search(_search_request(), context=forged_context)

    assert secrets.refs == []
    assert transport.calls == 0
    assert "never-print-this-secret" not in str(failure.value)


def test_secret_provider_failures_are_redacted_from_exception_chains(tmp_path) -> None:
    class FailingSecrets:
        def resolve(self, ref: SecretRef) -> str:
            raise RuntimeError("never-print-this-secret")

    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    context = authority.issue(
        "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
    )
    gateway = TavilyEvidenceGateway(
        budget=_budget(),
        lease_store=SqliteTavilyKeyLeaseStore(
            tmp_path / "redacted-leases.sqlite3", server_secret=b"s" * 32
        ),
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=RecordingTransport(),
        secret_provider=FailingSecrets(),
        clock=lambda: 100.0,
    )

    with pytest.raises(EvidenceGatewayAuthenticationError) as failure:
        gateway.search(_search_request(), context=context)

    assert "never-print-this-secret" not in str(failure.value)
    assert failure.value.__cause__ is None
    assert failure.value.__context__ is not None
    assert failure.value.__suppress_context__ is True
