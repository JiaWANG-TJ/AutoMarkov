from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from automarkov.contracts.evidence import (
    TAVILY_SLOT_IDS,
    AblationExecutionPlanRef,
    CrawlEvidenceRequest,
    CrawlSafetyReview,
    EvidenceBudgetManifest,
    EvidenceOmissionBinding,
    EvidenceOmissionRecord,
    ExtractEvidenceRequest,
    SearchEvidenceRequest,
    TavilyLeasePoolManifest,
)
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.errors import ArtifactParentContractError
from automarkov.domain.models import ArtifactId
from automarkov.lifecycle import ArtifactReference
from automarkov.public import CommandAuthority, CommandPrincipalBinding
from automarkov.repository import InMemoryArtifactRepository
from automarkov.tavily_gateway import (
    NoEvidenceRoute,
    ProviderContractError,
    RepositoryEvidenceArtifactSink,
    SqliteTavilyKeyLeaseStore,
    TavilyEvidenceGateway,
    TavilyTransportResponse,
    create_evidence_route,
)


def _budget_payload() -> dict[str, object]:
    return {
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
    }


def _common_request() -> dict[str, object]:
    return {
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
    }


def _crawl_review_payload(
    *, root_url: str = "https://docs.example.org/"
) -> dict[str, object]:
    parsed = urlsplit(root_url)
    return {
        "schema_version": "automarkov.crawl-safety-review.v1",
        "reviewed_root_url": root_url,
        "reviewed_origin_url": f"{parsed.scheme}://{parsed.netloc}/",
        "reviewed_domain": parsed.hostname,
        "domain_policy_passed": True,
        "robots_policy_passed": True,
        "license_policy_passed": True,
        "ssrf_policy_passed": True,
    }


def _crawl(
    *,
    review_ref: ArtifactReference | None = None,
    root_url: str = "https://docs.example.org/",
) -> CrawlEvidenceRequest:
    return CrawlEvidenceRequest.model_validate(
        {
            **_common_request(),
            "schema_version": "automarkov.tavily-crawl-request.v2",
            "endpoint": "/crawl",
            "root_url": root_url,
            "safety_review_ref": (
                review_ref
                or ArtifactReference(
                    artifact_id="artifact_" + "5" * 64,
                    payload_hash="sha256:" + "5" * 64,
                )
            ).model_dump(mode="json"),
            "allow_external": False,
            "include_usage": True,
            "include_images": False,
            "max_depth": 2,
            "max_breadth": 10,
            "limit": 20,
            "timeout_seconds": 10.5,
            "select_paths": ["/guide"],
            "exclude_paths": ["/private"],
        },
        strict=True,
    )


def test_versioned_requests_close_provider_defaults_before_network() -> None:
    budget = EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True)
    assert budget.endpoints == ("/crawl", "/extract", "/search")

    search = SearchEvidenceRequest.model_validate(
        {
            **_common_request(),
            "schema_version": "automarkov.tavily-search-request.v2",
            "endpoint": "/search",
            "query": "official transition semantics",
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
    assert search.include_answer is False

    with pytest.raises(ValidationError):
        SearchEvidenceRequest.model_validate(
            {**search.model_dump(mode="json"), "include_answer": True}, strict=True
        )
    with pytest.raises(ValidationError):
        SearchEvidenceRequest.model_validate(
            {**search.model_dump(mode="json"), "include_usage": False}, strict=True
        )

    extract = ExtractEvidenceRequest.model_validate(
        {
            **_common_request(),
            "schema_version": "automarkov.tavily-extract-request.v1",
            "endpoint": "/extract",
            "urls": ["https://docs.example.org/spec"],
            "include_usage": True,
            "include_images": False,
            "extract_depth": "basic",
            "format": "markdown",
            "timeout_seconds": 10.5,
        },
        strict=True,
    )
    assert extract.urls == ("https://docs.example.org/spec",)

    crawl = _crawl()
    assert crawl.allow_external is False
    with pytest.raises(ValidationError):
        CrawlEvidenceRequest.model_validate(
            {**crawl.model_dump(mode="json"), "instructions": "follow links"},
            strict=True,
        )


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "192.0.2.1",
        "224.0.0.1",
    ],
)
def test_budget_and_crawl_request_reject_ipv4_literals(host: str) -> None:
    budget_payload = _budget_payload()
    budget_payload.update(
        {
            "allowed_domains": [host],
            "allowed_url_prefixes": [f"https://{host}/"],
        }
    )
    with pytest.raises(ValidationError):
        EvidenceBudgetManifest.model_validate(budget_payload, strict=True)
    with pytest.raises(ValidationError):
        _crawl(root_url=f"https://{host}/")


@pytest.mark.parametrize(
    "root_url",
    [
        "https://[::]/",
        "https://[::1]/",
        "https://[fc00::1]/",
        "https://[fe80::1]/",
        "https://[ff02::1]/",
    ],
)
def test_crawl_request_rejects_ipv6_literals(root_url: str) -> None:
    with pytest.raises(ValidationError):
        _crawl(root_url=root_url)


class _Secrets:
    def resolve(self, ref) -> str:
        return "runtime-only-secret"


class _StatusTransport:
    def __init__(self, statuses: list[int | bytes]) -> None:
        self.statuses = statuses

    def send(self, **kwargs) -> TavilyTransportResponse:
        status = self.statuses.pop(0)
        if isinstance(status, bytes):
            return TavilyTransportResponse(status_code=200, headers={}, body=status)
        if status == 200:
            body = (
                b'{"request_id":"provider-ok","answer":null,'
                b'"results":[],"usage":{"credits":1.0}}'
            )
        else:
            body = b'{"request_id":"provider-error"}'
        return TavilyTransportResponse(status_code=status, headers={}, body=body)


def _search() -> SearchEvidenceRequest:
    return SearchEvidenceRequest.model_validate(
        {
            **_common_request(),
            "schema_version": "automarkov.tavily-search-request.v2",
            "endpoint": "/search",
            "query": "official transition semantics",
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


def _gateway(
    tmp_path,
    statuses: list[int | bytes],
    *,
    artifact_sink=None,
    crawl_review_verifier=None,
):
    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    context = authority.issue(
        "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
    )
    gateway = TavilyEvidenceGateway(
        budget=EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True),
        lease_store=SqliteTavilyKeyLeaseStore(
            tmp_path / "leases.sqlite3", server_secret=b"s" * 32
        ),
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=_StatusTransport(statuses),
        secret_provider=_Secrets(),
        clock=lambda: 100.0,
        artifact_sink=artifact_sink,
        crawl_review_verifier=crawl_review_verifier,
    )
    return gateway, context


def test_crawl_requires_a_trusted_exact_safety_review_before_network(tmp_path) -> None:
    request = _crawl()
    unavailable, context = _gateway(tmp_path / "unavailable", [200])
    with pytest.raises(ProviderContractError, match="crawl_safety_review_unavailable"):
        unavailable.crawl(request, context=context)

    mismatched_review = CrawlSafetyReview.model_validate(
        _crawl_review_payload(root_url="https://other.example.org/"), strict=True
    )
    mismatched, mismatched_context = _gateway(
        tmp_path / "mismatched",
        [200],
        crawl_review_verifier=lambda reference: mismatched_review,
    )
    with pytest.raises(
        ProviderContractError, match="crawl_safety_review_binding_mismatch"
    ):
        mismatched.crawl(request, context=mismatched_context)

    review = CrawlSafetyReview.model_validate(_crawl_review_payload(), strict=True)
    verified, verified_context = _gateway(
        tmp_path / "verified",
        [200],
        crawl_review_verifier=lambda reference: review,
    )
    assert verified.crawl(request, context=verified_context).outcome == "available"


def test_crawl_safety_review_requires_all_four_passed_conclusions() -> None:
    for field_name in (
        "domain_policy_passed",
        "robots_policy_passed",
        "license_policy_passed",
        "ssrf_policy_passed",
    ):
        payload = _crawl_review_payload()
        payload[field_name] = False
        with pytest.raises(ValidationError):
            CrawlSafetyReview.model_validate(payload, strict=True)


def test_status_router_rotates_401_but_stops_403_without_fallback(tmp_path) -> None:
    gateway, context = _gateway(tmp_path / "rotate", [401, 200])
    result = gateway.search(_search(), context=context)
    assert result.outcome == "available"
    assert [receipt.http_status for receipt in result.attempt_receipts] == [401, 200]
    assert len({receipt.slot_id for receipt in result.attempt_receipts}) == 2

    forbidden, forbidden_context = _gateway(tmp_path / "forbidden", [403, 200])
    blocked = forbidden.search(_search(), context=forbidden_context)
    assert blocked.outcome == "blocked"
    assert [receipt.http_status for receipt in blocked.attempt_receipts] == [403]
    assert "runtime-only-secret" not in repr(blocked)


def test_432_without_exact_usage_credit_receipt_cannot_claim_exhaustion(
    tmp_path,
) -> None:
    class EmptyUsageExhaustionTransport:
        def send(self, **kwargs) -> TavilyTransportResponse:
            return TavilyTransportResponse(
                status_code=432,
                headers={},
                body=b'{"request_id":"provider-exhausted","usage":{}}',
            )

    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    lease_store = SqliteTavilyKeyLeaseStore(
        tmp_path / "unreceipted-exhaustion.sqlite3", server_secret=b"s" * 32
    )
    gateway = TavilyEvidenceGateway(
        budget=EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True),
        lease_store=lease_store,
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=EmptyUsageExhaustionTransport(),
        secret_provider=_Secrets(),
        clock=lambda: 100.0,
    )

    result = gateway.search(
        _search(),
        context=authority.issue(
            "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
        ),
    )

    assert result.outcome == "blocked"
    assert all(
        lease_store.slot_state(receipt.slot_id).state == "INVALID"
        for receipt in result.attempt_receipts
    )


def test_429_obeys_retry_after_plus_bounded_full_jitter(tmp_path) -> None:
    class RetryTransport(_StatusTransport):
        def send(self, **kwargs) -> TavilyTransportResponse:
            response = super().send(**kwargs)
            if response.status_code == 429:
                return TavilyTransportResponse(
                    status_code=429,
                    headers={"retry-after": "2.0"},
                    body=response.body,
                )
            return response

    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    lease_store = SqliteTavilyKeyLeaseStore(
        tmp_path / "retry-leases.sqlite3", server_secret=b"s" * 32
    )
    gateway = TavilyEvidenceGateway(
        budget=EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True),
        lease_store=lease_store,
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=RetryTransport([429, 200]),
        secret_provider=_Secrets(),
        clock=lambda: 100.0,
        jitter=lambda lower, upper: upper,
    )
    result = gateway.search(
        _search(),
        context=authority.issue(
            "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
        ),
    )
    cooled_slot = result.attempt_receipts[0].slot_id
    assert result.outcome == "available"
    assert lease_store.slot_state(cooled_slot).available_at == 104.0


def test_429_provider_retry_after_above_local_backoff_cap_is_not_shortened(
    tmp_path,
) -> None:
    class RetryTransport(_StatusTransport):
        def send(self, **kwargs) -> TavilyTransportResponse:
            response = super().send(**kwargs)
            if response.status_code == 429:
                return TavilyTransportResponse(
                    status_code=429,
                    headers={"retry-after": "12.0"},
                    body=response.body,
                )
            return response

    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    lease_store = SqliteTavilyKeyLeaseStore(
        tmp_path / "long-retry-leases.sqlite3", server_secret=b"s" * 32
    )
    gateway = TavilyEvidenceGateway(
        budget=EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True),
        lease_store=lease_store,
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=RetryTransport([429, 200]),
        secret_provider=_Secrets(),
        clock=lambda: 100.0,
        jitter=lambda lower, upper: upper,
    )

    result = gateway.search(
        _search(),
        context=authority.issue(
            "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
        ),
    )

    cooled_slot = result.attempt_receipts[0].slot_id
    assert result.outcome == "available"
    assert lease_store.slot_state(cooled_slot).available_at == 116.5


@pytest.mark.parametrize("retry_after", ["Infinity", "NaN"])
def test_429_rejects_nonfinite_retry_after_without_poisoning_the_lease(
    tmp_path, retry_after: str
) -> None:
    class NonfiniteRetryTransport:
        def send(self, **kwargs) -> TavilyTransportResponse:
            return TavilyTransportResponse(
                status_code=429,
                headers={"retry-after": retry_after},
                body=b'{"request_id":"provider-error"}',
            )

    gateway, context = _gateway(tmp_path, [429])
    gateway._transport = NonfiniteRetryTransport()

    with pytest.raises(ProviderContractError, match="retry_after_invalid"):
        gateway.search(_search(), context=context)


class _RecordingSink:
    def __init__(self) -> None:
        self.records = []

    @property
    def raw_store_ref(self):
        return _search().generation_evidence_view.stores[0]

    def put(self, artifact_type, payload, parents):
        index = len(self.records) + 10
        reference = ArtifactReference(
            artifact_id="artifact_" + f"{index:064x}",
            payload_hash="sha256:" + f"{index:064x}",
        )
        self.records.append((artifact_type, payload, parents, reference))
        return reference


def test_available_result_persists_the_closed_request_attempt_snapshot_ledger_dag(
    tmp_path,
) -> None:
    sink = _RecordingSink()
    gateway, context = _gateway(tmp_path, [200], artifact_sink=sink)
    result = gateway.search(_search(), context=context)

    assert [record[0] for record in sink.records] == [
        "tavily_search_request",
        "provider_attempt_receipt",
        "evidence_snapshot",
        "evidence_ledger",
    ]
    request_ref = sink.records[0][3]
    attempt_ref = sink.records[1][3]
    snapshot_ref = sink.records[2][3]
    assert {parent.artifact_id for parent in sink.records[0][2]} == {
        _search().task_input_ref.artifact_id,
        _search().budget_ref.artifact_id,
        _search().lease_pool_ref.artifact_id,
    }
    assert sink.records[1][2] == (request_ref,)
    assert sink.records[2][2] == (request_ref, attempt_ref)
    assert sink.records[3][2] == (request_ref, snapshot_ref)
    assert result.request_ref == request_ref
    assert result.attempt_receipt_refs == (attempt_ref,)
    assert result.snapshot_ref == snapshot_ref
    assert result.ledger_revision_ref == sink.records[3][3]


def test_repository_sink_enforces_the_evidence_artifact_dag(tmp_path) -> None:
    repository = InMemoryArtifactRepository()

    def put_root(artifact_type: str, payload: dict[str, object]) -> ArtifactReference:
        result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(payload),
                "parent_artifact_ids": [],
                "created_by": "principal_researcher",
                "created_at": "2026-08-12T00:00:00Z",
                "source_evidence_ids": [],
            }
        )
        return ArtifactReference(
            artifact_id=result.artifact_id.root,
            payload_hash=result.payload_hash.root,
        )

    task_ref = put_root(
        "task_request",
        {
            "schema_version": "automarkov.task-request.v1",
            "request_id": "request_evidence",
            "task_text": "Find the official contract.",
            "budget": {
                "schema_version": "automarkov.request-budget.v1",
                "wall_time_seconds": 30,
                "llm_token_limit": 0,
                "tool_call_limit": 5,
            },
            "permissions": {
                "schema_version": "automarkov.request-permissions.v1",
                "allow_retrieval": True,
                "allow_clarification": False,
                "allow_code_execution": False,
            },
        },
    )
    budget_ref = put_root("evidence_budget_manifest", _budget_payload())
    lease_ref = put_root(
        "tavily_lease_pool_manifest",
        TavilyLeasePoolManifest(
            schema_version="automarkov.tavily-lease-pool-manifest.v1",
            provider="tavily",
            slot_ids=TAVILY_SLOT_IDS,
        ).model_dump(mode="json"),
    )
    request_payload = _search().model_dump(mode="json")
    request_payload.update(
        {
            "task_input_ref": task_ref.model_dump(mode="json"),
            "budget_ref": budget_ref.model_dump(mode="json"),
            "lease_pool_ref": lease_ref.model_dump(mode="json"),
        }
    )
    request = SearchEvidenceRequest.model_validate(request_payload, strict=True)
    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    gateway = TavilyEvidenceGateway(
        budget=EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True),
        lease_store=SqliteTavilyKeyLeaseStore(
            tmp_path / "repository-leases.sqlite3", server_secret=b"s" * 32
        ),
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=_StatusTransport([200]),
        secret_provider=_Secrets(),
        clock=lambda: 100.0,
        artifact_sink=RepositoryEvidenceArtifactSink(
            repository,
            created_by="principal_researcher",
            timestamp=lambda: "2026-08-12T00:00:00Z",
            raw_store_ref=request.generation_evidence_view.stores[0],
        ),
    )
    result = gateway.search(
        request,
        context=authority.issue(
            "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
        ),
    )
    assert result.ledger_revision_ref is not None
    ledger_parents = repository.lineage(
        ArtifactId(root=result.ledger_revision_ref.artifact_id)
    ).artifact_ids
    assert result.request_ref is not None
    assert result.snapshot_ref is not None
    assert {item.root for item in ledger_parents} == {
        result.request_ref.artifact_id,
        result.snapshot_ref.artifact_id,
    }
    assert {
        item.root
        for item in repository.lineage(
            ArtifactId(root=result.snapshot_ref.artifact_id)
        ).artifact_ids
    } == {
        result.request_ref.artifact_id,
        result.attempt_receipt_refs[0].artifact_id,
    }
    assert repository.lineage(
        ArtifactId(root=result.attempt_receipt_refs[0].artifact_id)
    ).artifact_ids == (ArtifactId(root=result.request_ref.artifact_id),)
    assert {
        item.root
        for item in repository.lineage(
            ArtifactId(root=result.request_ref.artifact_id)
        ).artifact_ids
    } == {
        task_ref.artifact_id,
        budget_ref.artifact_id,
        lease_ref.artifact_id,
    }


def test_default_repository_binds_crawl_request_to_content_addressed_review() -> None:
    repository = InMemoryArtifactRepository()

    def put_root(artifact_type: str, payload: dict[str, object]) -> ArtifactReference:
        result = repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(payload),
                "parent_artifact_ids": [],
                "created_by": "principal_researcher",
                "created_at": "2026-08-12T00:00:00Z",
                "source_evidence_ids": [],
            }
        )
        return ArtifactReference(
            artifact_id=result.artifact_id.root,
            payload_hash=result.payload_hash.root,
        )

    task_ref = put_root(
        "task_request",
        {
            "schema_version": "automarkov.task-request.v1",
            "request_id": "request_crawl_review",
            "task_text": "Crawl the reviewed official documentation.",
            "budget": {
                "schema_version": "automarkov.request-budget.v1",
                "wall_time_seconds": 30,
                "llm_token_limit": 0,
                "tool_call_limit": 5,
            },
            "permissions": {
                "schema_version": "automarkov.request-permissions.v1",
                "allow_retrieval": True,
                "allow_clarification": False,
                "allow_code_execution": False,
            },
        },
    )
    budget_ref = put_root("evidence_budget_manifest", _budget_payload())
    lease_ref = put_root(
        "tavily_lease_pool_manifest",
        TavilyLeasePoolManifest(
            schema_version="automarkov.tavily-lease-pool-manifest.v1",
            provider="tavily",
            slot_ids=TAVILY_SLOT_IDS,
        ).model_dump(mode="json"),
    )
    review_ref = put_root("crawl_safety_review", _crawl_review_payload())
    request_payload = _crawl(review_ref=review_ref).model_dump(mode="json")
    request_payload.update(
        {
            "task_input_ref": task_ref.model_dump(mode="json"),
            "budget_ref": budget_ref.model_dump(mode="json"),
            "lease_pool_ref": lease_ref.model_dump(mode="json"),
        }
    )
    request = CrawlEvidenceRequest.model_validate(request_payload, strict=True)

    put_payload = {
        "schema_version": "automarkov.artifact-put-request.v2",
        "artifact_type": "tavily_crawl_request",
        "payload_bytes": canonical_json_bytes(request.model_dump(mode="json")),
        "created_by": "principal_researcher",
        "created_at": "2026-08-12T00:00:00Z",
        "source_evidence_ids": [],
    }
    base_parent_ids = sorted(
        (task_ref.artifact_id, budget_ref.artifact_id, lease_ref.artifact_id)
    )
    with pytest.raises(ArtifactParentContractError):
        repository.put({**put_payload, "parent_artifact_ids": base_parent_ids})

    expected_parent_ids = sorted((*base_parent_ids, review_ref.artifact_id))
    result = repository.put({**put_payload, "parent_artifact_ids": expected_parent_ids})
    assert {
        parent.root for parent in repository.lineage(result.artifact_id).artifact_ids
    } == set(expected_parent_ids)


def test_extract_persists_allowed_raw_documents_between_attempt_and_snapshot(
    tmp_path,
) -> None:
    request = ExtractEvidenceRequest.model_validate(
        {
            **_common_request(),
            "schema_version": "automarkov.tavily-extract-request.v1",
            "endpoint": "/extract",
            "urls": ["https://docs.example.org/spec"],
            "include_usage": True,
            "include_images": False,
            "extract_depth": "basic",
            "format": "markdown",
            "timeout_seconds": 10.5,
        },
        strict=True,
    )
    body = b"".join(
        (
            b'{"request_id":"provider-extract","results":[',
            b'{"url":"https://docs.example.org/spec","raw_content":"source"}],',
            b'"failed_results":[],"usage":{"credits":1}}',
        )
    )
    sink = _RecordingSink()
    authority = CommandAuthority(
        "authority_evidence",
        (CommandPrincipalBinding("principal_researcher", "process_research"),),
    )
    gateway = TavilyEvidenceGateway(
        budget=EvidenceBudgetManifest.model_validate(_budget_payload(), strict=True),
        lease_store=SqliteTavilyKeyLeaseStore(
            tmp_path / "extract-leases.sqlite3", server_secret=b"s" * 32
        ),
        command_authority=authority,
        expected_process_execution_id="process_research",
        evidence_view_verifier=lambda view: view,
        transport=_StatusTransport([body]),
        secret_provider=_Secrets(),
        clock=lambda: 100.0,
        artifact_sink=sink,
    )
    result = gateway.extract(
        request,
        context=authority.issue(
            "principal_researcher", "process_research", "2026-08-12T00:00:00Z"
        ),
    )
    assert [record[0] for record in sink.records] == [
        "tavily_extract_request",
        "provider_attempt_receipt",
        "raw_evidence_document",
        "evidence_snapshot",
        "evidence_ledger",
    ]
    assert sink.records[2][1].document.store_tier == "allowed_evidence"
    assert result.raw_document_refs == (sink.records[2][3],)


@pytest.mark.parametrize(
    "body",
    [
        b"".join(
            (
                b'{"request_id":"provider-bad","answer":"hosted synthesis",',
                b'"results":[],"usage":{"credits":1.0}}',
            )
        ),
        b'{"request_id":"provider-bad","answer":null,"results":[]}',
    ],
)
def test_hosted_answer_or_missing_usage_fails_closed(body, tmp_path) -> None:
    gateway, context = _gateway(tmp_path, [body])
    result = gateway.search(_search(), context=context)
    assert result.outcome == "blocked"
    assert result.reason_code == "provider_contract_violation"
    assert result.snapshot is None
    assert result.attempt_receipts[0].cost_state == "ambiguous"
    assert result.attempt_receipts[0].usage_credits is None


def test_only_exact_no_evidence_plan_skips_gateway_construction() -> None:
    plan_ref = {
        "artifact_id": "artifact_" + "5" * 64,
        "payload_hash": "sha256:" + "5" * 64,
    }
    pair_ref = {
        "artifact_id": "artifact_" + "6" * 64,
        "payload_hash": "sha256:" + "6" * 64,
    }
    omission_ref = {
        "artifact_id": "artifact_" + "7" * 64,
        "payload_hash": "sha256:" + "7" * 64,
    }
    plan = AblationExecutionPlanRef.model_validate(
        {
            "schema_version": "automarkov.ablation-execution-plan-ref.v1",
            "ablation_method_id": "automarkov_no_evidence",
            "plan_ref": plan_ref,
            "pair_binding_ref": pair_ref,
            "allow_retrieval": False,
        },
        strict=True,
    )
    record = EvidenceOmissionRecord.model_validate(
        {
            "schema_version": "automarkov.evidence-omission.v1",
            "record_kind": "evidence_omitted_by_design",
            "experiment_id": "experiment_001",
            "run_id": "run_001",
            "cell_id": "cell_001",
            "task_card_ref": _common_request()["task_input_ref"],
            "ablation_execution_plan_ref": plan_ref,
            "pair_binding_ref": pair_ref,
            "ablation_method_id": "automarkov_no_evidence",
            "omitted_gate_id": "EVIDENCE_LEDGER_CLOSURE",
            "reason": "controlled_ablation",
        },
        strict=True,
    )
    binding = EvidenceOmissionBinding.model_validate(
        {
            "schema_version": "automarkov.evidence-omission-binding.v1",
            "binding_kind": "omitted_by_design",
            "omission_record_ref": omission_ref,
            "ablation_method_id": "automarkov_no_evidence",
            "omitted_gate_id": "EVIDENCE_LEDGER_CLOSURE",
        },
        strict=True,
    )
    constructions = 0

    def forbidden_factory():
        nonlocal constructions
        constructions += 1
        raise AssertionError("no-evidence branch constructed a retrieval capability")

    route = create_evidence_route(
        method_id="automarkov_no_evidence",
        allow_retrieval=False,
        ablation_plan=plan,
        omission_record=record,
        omission_record_ref=binding.omission_record_ref,
        omission_binding=binding,
        gateway_factory=forbidden_factory,
    )
    assert isinstance(route, NoEvidenceRoute)
    assert route.binding == binding
    assert constructions == 0

    with pytest.raises(ProviderContractError):
        create_evidence_route(
            method_id="automarkov_full",
            allow_retrieval=False,
            ablation_plan=None,
            omission_record=None,
            omission_record_ref=None,
            omission_binding=None,
            gateway_factory=forbidden_factory,
        )
