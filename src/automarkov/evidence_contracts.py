from __future__ import annotations

from ipaddress import ip_address
from typing import Annotated, Literal, Self, TypeAlias
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator

from automarkov.canonical import (
    FrozenSequence,
    NonNegativeCanonicalFloat,
    NonNegativeSafeCanonicalInt,
    PositiveSafeCanonicalInt,
    StrictCanonicalFloat,
    StrictFalse,
    StrictTrue,
)
from automarkov.domain import (
    EvidenceStoreRef,
    GenerationEvidenceView,
    StrictFrozenModel,
)
from automarkov.lifecycle import ArtifactReference

PositiveInt = PositiveSafeCanonicalInt
NonNegativeInt = NonNegativeSafeCanonicalInt
PositiveFloat = StrictCanonicalFloat
NonNegativeFloat = NonNegativeCanonicalFloat
CreditAmount: TypeAlias = NonNegativeSafeCanonicalInt | NonNegativeCanonicalFloat
NonEmptyId = Annotated[
    str,
    Field(
        strict=True,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
RequestId = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^evidence_request_[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
SlotId = Annotated[
    str,
    Field(strict=True, pattern=r"^TAVILY_API_KEY_(?:0[1-9]|1[0-9]|2[0-9])$"),
]
Sha256Value = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
CanonicalUrl = Annotated[str, Field(strict=True, min_length=9, max_length=8_192)]

TAVILY_SLOT_IDS: tuple[str, ...] = tuple(
    f"TAVILY_API_KEY_{index:02d}" for index in range(1, 30)
)
TAVILY_ENDPOINTS: tuple[str, ...] = ("/crawl", "/extract", "/search")


def _canonical_tuple(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    expected = tuple(sorted(set(values), key=lambda item: item.encode("utf-8")))
    if values != expected:
        raise ValueError(f"{label} must be sorted and unique")
    return values


def _require_https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.hostname != parsed.hostname.lower()
    ):
        raise ValueError("evidence URLs must be canonical credential-free HTTPS URLs")
    try:
        ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("evidence URLs must use reviewed DNS names, not IP literals")
    canonical_netloc = parsed.hostname
    if parsed.port is not None:
        if parsed.port == 443:
            raise ValueError("canonical HTTPS URLs omit the default port")
        canonical_netloc += f":{parsed.port}"
    canonical = urlunsplit(
        ("https", canonical_netloc, parsed.path or "/", parsed.query, "")
    )
    if value != canonical:
        raise ValueError("evidence URL is not canonical")
    return value


def _require_domain(value: str) -> str:
    try:
        ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("evidence domains cannot be IP literals")
    if (
        not value
        or value != value.lower()
        or value.startswith(".")
        or value.endswith(".")
        or "/" in value
        or ":" in value
        or value == "localhost"
        or any(not label or len(label) > 63 for label in value.split("."))
        or any(
            not all(
                character.isascii() and (character.isalnum() or character == "-")
                for character in label
            )
            or label.startswith("-")
            or label.endswith("-")
            for label in value.split(".")
        )
    ):
        raise ValueError("evidence domains must use canonical lowercase DNS names")
    return value


class EvidenceBudgetManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-budget-manifest.v1"]
    pair_binding_id: NonEmptyId
    provider: Literal["tavily"]
    api_origin: Literal["https://api.tavily.com"]
    endpoints: FrozenSequence[Literal["/crawl", "/extract", "/search"]]
    credit_ceiling: PositiveInt
    logical_call_ceiling: PositiveInt
    attempt_ceiling_per_call: PositiveInt
    key_lease_wait_seconds: PositiveFloat
    non_crawl_requests_per_minute: PositiveInt
    crawl_requests_per_minute: PositiveInt
    search_max_results: PositiveInt
    extract_max_urls: PositiveInt
    crawl_max_depth: PositiveInt
    crawl_max_breadth: PositiveInt
    crawl_page_limit: PositiveInt
    retry_base_seconds: PositiveFloat
    retry_max_seconds: PositiveFloat
    full_jitter: StrictTrue
    allowed_domains: FrozenSequence[str]
    blocked_domains: FrozenSequence[str]
    allowed_url_prefixes: FrozenSequence[CanonicalUrl]
    blocked_path_prefixes: FrozenSequence[str]

    @model_validator(mode="after")
    def require_closed_budget(self) -> Self:
        if any(
            value <= 0.0
            for value in (
                self.key_lease_wait_seconds,
                self.retry_base_seconds,
                self.retry_max_seconds,
            )
        ):
            raise ValueError("budget time limits must be positive")
        if tuple(self.endpoints) != TAVILY_ENDPOINTS:
            raise ValueError("the Tavily endpoint allowlist must be exact")
        for field_name in (
            "allowed_domains",
            "blocked_domains",
            "allowed_url_prefixes",
            "blocked_path_prefixes",
        ):
            values = tuple(getattr(self, field_name))
            _canonical_tuple(values, label=field_name)
            if any(not value for value in values):
                raise ValueError(f"{field_name} cannot contain blank values")
        for domain in (*self.allowed_domains, *self.blocked_domains):
            _require_domain(domain)
        if set(self.allowed_domains) & set(self.blocked_domains):
            raise ValueError("allowed and blocked domains must not overlap")
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry base cannot exceed retry maximum")
        for url in self.allowed_url_prefixes:
            _require_https_url(url)
        return self


class TavilyLeasePoolManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.tavily-lease-pool-manifest.v1"]
    provider: Literal["tavily"]
    slot_ids: FrozenSequence[SlotId]

    @model_validator(mode="after")
    def require_exact_slots(self) -> Self:
        if tuple(self.slot_ids) != TAVILY_SLOT_IDS:
            raise ValueError(
                "Tavily lease pool must contain exactly slots 01 through 29"
            )
        return self


class _EvidenceRequestBase(StrictFrozenModel):
    request_id: RequestId
    run_id: NonEmptyId
    pair_binding_id: NonEmptyId
    task_input_ref: ArtifactReference
    budget_ref: ArtifactReference
    lease_pool_ref: ArtifactReference
    generation_evidence_view: GenerationEvidenceView

    @model_validator(mode="after")
    def require_allowed_evidence_view(self) -> Self:
        if (
            self.generation_evidence_view.capability_grant.principal_kind
            != "researcher"
            or any(
                store.tier != "allowed_evidence"
                for store in self.generation_evidence_view.stores
            )
        ):
            raise ValueError(
                "Tavily requests require a researcher allowed-evidence view"
            )
        return self


class SearchEvidenceRequest(_EvidenceRequestBase):
    schema_version: Literal["automarkov.tavily-search-request.v2"]
    endpoint: Literal["/search"]
    query: Annotated[str, Field(strict=True, min_length=1, max_length=10_000)]
    include_answer: StrictFalse
    include_usage: StrictTrue
    include_raw_content: StrictFalse
    include_images: StrictFalse
    auto_parameters: StrictFalse
    search_depth: Literal["basic", "advanced"]
    max_results: PositiveInt
    include_domains: FrozenSequence[str]
    exclude_domains: FrozenSequence[str]

    @model_validator(mode="after")
    def require_domain_sets(self) -> Self:
        _canonical_tuple(tuple(self.include_domains), label="include_domains")
        _canonical_tuple(tuple(self.exclude_domains), label="exclude_domains")
        if set(self.include_domains) & set(self.exclude_domains):
            raise ValueError("included and excluded domains must not overlap")
        for domain in (*self.include_domains, *self.exclude_domains):
            _require_domain(domain)
        return self


class ExtractEvidenceRequest(_EvidenceRequestBase):
    schema_version: Literal["automarkov.tavily-extract-request.v1"]
    endpoint: Literal["/extract"]
    urls: FrozenSequence[CanonicalUrl]
    include_usage: StrictTrue
    include_images: StrictFalse
    extract_depth: Literal["basic", "advanced"]
    format: Literal["markdown", "text"]
    timeout_seconds: PositiveFloat

    @model_validator(mode="after")
    def require_unique_urls(self) -> Self:
        if self.timeout_seconds <= 0.0:
            raise ValueError("extract timeout must be positive")
        if not self.urls:
            raise ValueError("extract requires at least one URL")
        _canonical_tuple(tuple(self.urls), label="extract URLs")
        for url in self.urls:
            _require_https_url(url)
        return self


class CrawlSafetyReview(StrictFrozenModel):
    schema_version: Literal["automarkov.crawl-safety-review.v1"]
    reviewed_root_url: CanonicalUrl
    reviewed_origin_url: CanonicalUrl
    reviewed_domain: str
    domain_policy_passed: StrictTrue
    robots_policy_passed: StrictTrue
    license_policy_passed: StrictTrue
    ssrf_policy_passed: StrictTrue

    @model_validator(mode="after")
    def require_exact_review_scope(self) -> Self:
        _require_https_url(self.reviewed_root_url)
        _require_https_url(self.reviewed_origin_url)
        _require_domain(self.reviewed_domain)
        root = urlsplit(self.reviewed_root_url)
        expected_origin = urlunsplit((root.scheme, root.netloc, "/", "", ""))
        if (
            self.reviewed_domain != root.hostname
            or self.reviewed_origin_url != expected_origin
        ):
            raise ValueError(
                "crawl review domain and origin must exactly bind the reviewed root"
            )
        return self


class CrawlEvidenceRequest(_EvidenceRequestBase):
    schema_version: Literal["automarkov.tavily-crawl-request.v2"]
    endpoint: Literal["/crawl"]
    root_url: CanonicalUrl
    safety_review_ref: ArtifactReference
    allow_external: StrictFalse
    include_usage: StrictTrue
    include_images: StrictFalse
    max_depth: PositiveInt
    max_breadth: PositiveInt
    limit: PositiveInt
    timeout_seconds: PositiveFloat
    select_paths: FrozenSequence[str]
    exclude_paths: FrozenSequence[str]

    @model_validator(mode="after")
    def require_reviewed_crawl(self) -> Self:
        if self.timeout_seconds <= 0.0:
            raise ValueError("crawl timeout must be positive")
        _require_https_url(self.root_url)
        _canonical_tuple(tuple(self.select_paths), label="select_paths")
        _canonical_tuple(tuple(self.exclude_paths), label="exclude_paths")
        if set(self.select_paths) & set(self.exclude_paths):
            raise ValueError("selected and excluded crawl paths must not overlap")
        if any(
            not path.startswith("/")
            for path in (*self.select_paths, *self.exclude_paths)
        ):
            raise ValueError("crawl path filters must be absolute paths")
        return self


EvidenceRequest: TypeAlias = (
    SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest
)


class ProviderAttemptReceipt(StrictFrozenModel):
    schema_version: Literal["automarkov.provider-attempt-receipt.v1"]
    slot_id: SlotId
    endpoint: Literal["/crawl", "/extract", "/search"]
    attempt_number: PositiveInt
    http_status: NonNegativeSafeCanonicalInt | None
    provider_request_id: (
        Annotated[str, Field(strict=True, min_length=1, max_length=256)] | None
    )
    request_hash: Sha256Value
    response_hash: Sha256Value | None
    duration_ms: NonNegativeInt
    usage_credits: CreditAmount | None
    credit_reservation: CreditAmount
    cost_state: Literal["settled", "ambiguous"]

    @model_validator(mode="after")
    def require_cost_evidence(self) -> Self:
        if self.credit_reservation <= 0.0:
            raise ValueError("credit reservation must be positive")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("HTTP status must be in the wire status range")
        if self.cost_state == "settled" and self.usage_credits is None:
            raise ValueError("settled attempts require provider usage")
        if self.cost_state == "ambiguous" and self.usage_credits is not None:
            raise ValueError("ambiguous attempts cannot invent provider usage")
        return self


class SearchDiscovery(StrictFrozenModel):
    title: Annotated[str, Field(strict=True, min_length=1, max_length=10_000)]
    url: CanonicalUrl
    snippet: Annotated[str, Field(strict=True, max_length=50_000)]

    @model_validator(mode="after")
    def require_url(self) -> Self:
        _require_https_url(self.url)
        return self


class RawEvidenceDocument(StrictFrozenModel):
    schema_version: Literal["automarkov.raw-evidence-document.v1"]
    source_url: CanonicalUrl
    content: Annotated[str, Field(strict=True, min_length=1, max_length=2_000_000)]
    content_hash: Sha256Value
    store_tier: Literal["allowed_evidence"]

    @model_validator(mode="after")
    def require_url(self) -> Self:
        _require_https_url(self.source_url)
        return self


class SearchSnapshot(StrictFrozenModel):
    schema_version: Literal["automarkov.search-snapshot.v1"]
    snapshot_kind: Literal["discovery_only"]
    request_id: RequestId
    discoveries: FrozenSequence[SearchDiscovery]


class ExtractSnapshot(StrictFrozenModel):
    schema_version: Literal["automarkov.extract-snapshot.v1"]
    request_id: RequestId
    documents: FrozenSequence[RawEvidenceDocument]
    failed_urls: FrozenSequence[CanonicalUrl]


class CrawlSnapshot(StrictFrozenModel):
    schema_version: Literal["automarkov.crawl-snapshot.v1"]
    request_id: RequestId
    root_url: CanonicalUrl
    documents: FrozenSequence[RawEvidenceDocument]

    @model_validator(mode="after")
    def require_same_origin(self) -> Self:
        root = urlsplit(self.root_url)
        if any(
            (urlsplit(document.source_url).scheme, urlsplit(document.source_url).netloc)
            != (root.scheme, root.netloc)
            for document in self.documents
        ):
            raise ValueError("crawl documents must remain on the reviewed origin")
        return self


EvidenceSnapshot: TypeAlias = SearchSnapshot | ExtractSnapshot | CrawlSnapshot


class EvidenceGatewayResult(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-gateway-result.v1"]
    outcome: Literal[
        "available",
        "temporarily_unavailable",
        "authority_required",
        "budget_exhausted",
        "blocked",
    ]
    request_id: RequestId
    snapshot: SearchSnapshot | ExtractSnapshot | CrawlSnapshot | None
    attempt_receipts: FrozenSequence[ProviderAttemptReceipt]
    request_ref: ArtifactReference | None
    attempt_receipt_refs: FrozenSequence[ArtifactReference]
    raw_document_refs: FrozenSequence[ArtifactReference]
    snapshot_ref: ArtifactReference | None
    ledger_revision_ref: ArtifactReference | None
    earliest_availability: Annotated[str, Field(strict=True, min_length=1)] | None
    reason_code: Annotated[str, Field(strict=True, min_length=1, max_length=160)] | None

    @model_validator(mode="after")
    def require_outcome_shape(self) -> Self:
        if (self.outcome == "available") != (self.snapshot is not None):
            raise ValueError("only available outcomes carry evidence snapshots")
        if (self.snapshot_ref is None) != (self.ledger_revision_ref is None):
            raise ValueError("snapshot and ledger references must be paired")
        if (
            self.outcome == "available"
            and self.request_ref is not None
            and (self.snapshot_ref is None or self.ledger_revision_ref is None)
        ):
            raise ValueError("persisted available results require the complete DAG")
        if self.request_ref is None and (
            self.attempt_receipt_refs or self.raw_document_refs
        ):
            raise ValueError(
                "unpersisted gateway results cannot expose orphan references"
            )
        if len(self.attempt_receipt_refs) not in {0, len(self.attempt_receipts)}:
            raise ValueError("attempt receipt references must be complete")
        if self.outcome == "temporarily_unavailable":
            if self.earliest_availability is None:
                raise ValueError("temporary unavailability requires a resume timestamp")
        elif self.earliest_availability is not None:
            raise ValueError("only temporary unavailability carries a resume timestamp")
        return self


class ProviderAttemptArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.provider-attempt-artifact.v1"]
    request_ref: ArtifactReference
    receipt: ProviderAttemptReceipt


class RawEvidenceDocumentArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.raw-evidence-document-artifact.v1"]
    attempt_ref: ArtifactReference
    allowed_store: EvidenceStoreRef
    document: RawEvidenceDocument

    @model_validator(mode="after")
    def require_allowed_store(self) -> Self:
        if self.allowed_store.tier != "allowed_evidence":
            raise ValueError("raw evidence documents require an Allowed Evidence Store")
        return self


class EvidenceSnapshotArtifact(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-snapshot-artifact.v1"]
    request_ref: ArtifactReference
    attempt_refs: FrozenSequence[ArtifactReference]
    raw_document_refs: FrozenSequence[ArtifactReference]
    snapshot: SearchSnapshot | ExtractSnapshot | CrawlSnapshot

    @model_validator(mode="after")
    def require_snapshot_lineage(self) -> Self:
        if not self.attempt_refs:
            raise ValueError("evidence snapshot requires at least one provider attempt")
        if len({item.artifact_id for item in self.attempt_refs}) != len(
            self.attempt_refs
        ):
            raise ValueError("attempt references must be unique")
        if len({item.artifact_id for item in self.raw_document_refs}) != len(
            self.raw_document_refs
        ):
            raise ValueError("raw document references must be unique")
        return self


class EvidenceLedgerRevision(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-ledger-revision.v1"]
    request_ref: ArtifactReference
    snapshot_ref: ArtifactReference
    revision_number: PositiveInt
    evidence_item_refs: FrozenSequence[ArtifactReference]


class AblationExecutionPlanRef(StrictFrozenModel):
    schema_version: Literal["automarkov.ablation-execution-plan-ref.v1"]
    ablation_method_id: Literal["automarkov_no_evidence"]
    plan_ref: ArtifactReference
    pair_binding_ref: ArtifactReference
    allow_retrieval: StrictFalse


class EvidenceOmissionRecord(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-omission.v1"]
    record_kind: Literal["evidence_omitted_by_design"]
    experiment_id: NonEmptyId
    run_id: NonEmptyId
    cell_id: NonEmptyId
    task_card_ref: ArtifactReference
    ablation_execution_plan_ref: ArtifactReference
    pair_binding_ref: ArtifactReference
    ablation_method_id: Literal["automarkov_no_evidence"]
    omitted_gate_id: Literal["EVIDENCE_LEDGER_CLOSURE"]
    reason: Literal["controlled_ablation"]


class EvidenceLedgerBinding(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-ledger-binding.v1"]
    binding_kind: Literal["ledger"]
    evidence_ledger_ref: ArtifactReference


class EvidenceOmissionBinding(StrictFrozenModel):
    schema_version: Literal["automarkov.evidence-omission-binding.v1"]
    binding_kind: Literal["omitted_by_design"]
    omission_record_ref: ArtifactReference
    ablation_method_id: Literal["automarkov_no_evidence"]
    omitted_gate_id: Literal["EVIDENCE_LEDGER_CLOSURE"]


__all__ = [
    "TAVILY_ENDPOINTS",
    "TAVILY_SLOT_IDS",
    "AblationExecutionPlanRef",
    "CrawlEvidenceRequest",
    "CrawlSafetyReview",
    "CrawlSnapshot",
    "EvidenceBudgetManifest",
    "EvidenceGatewayResult",
    "EvidenceLedgerBinding",
    "EvidenceLedgerRevision",
    "EvidenceOmissionBinding",
    "EvidenceOmissionRecord",
    "EvidenceRequest",
    "EvidenceSnapshot",
    "EvidenceSnapshotArtifact",
    "ExtractEvidenceRequest",
    "ExtractSnapshot",
    "ProviderAttemptArtifact",
    "ProviderAttemptReceipt",
    "RawEvidenceDocument",
    "RawEvidenceDocumentArtifact",
    "SearchDiscovery",
    "SearchEvidenceRequest",
    "SearchSnapshot",
    "TavilyLeasePoolManifest",
]
