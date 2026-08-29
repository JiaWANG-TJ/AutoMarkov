from __future__ import annotations

import hmac
import importlib
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from pathlib import Path
from random import SystemRandom
from secrets import token_hex
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit

from automarkov.contracts.evidence import (
    TAVILY_SLOT_IDS,
    AblationExecutionPlanRef,
    CrawlEvidenceRequest,
    CrawlSafetyReview,
    CrawlSnapshot,
    EvidenceBudgetManifest,
    EvidenceGatewayResult,
    EvidenceLedgerRevision,
    EvidenceOmissionBinding,
    EvidenceOmissionRecord,
    EvidenceSnapshotArtifact,
    ExtractEvidenceRequest,
    ExtractSnapshot,
    ProviderAttemptArtifact,
    ProviderAttemptReceipt,
    RawEvidenceDocument,
    RawEvidenceDocumentArtifact,
    SearchEvidenceRequest,
    SearchSnapshot,
)
from automarkov.domain.canonical import canonical_json_bytes, parse_json_payload
from automarkov.domain.errors import (
    EvidenceBudgetLimitError,
    EvidenceGatewayAuthenticationError,
    EvidenceProviderContractError,
)
from automarkov.domain.models import (
    EvidenceStoreRef,
    GenerationEvidenceView,
    StrictFrozenModel,
)
from automarkov.lifecycle import ArtifactReference
from automarkov.public import (
    ArtifactRepository,
    AuthenticatedCommandContext,
    CommandAuthority,
)

Endpoint = Literal["/crawl", "/extract", "/search"]
SlotStateValue = Literal["AVAILABLE", "COOLDOWN", "EXHAUSTED", "INVALID"]


@dataclass(frozen=True, slots=True)
class KeyLease:
    slot_id: str
    endpoint: Endpoint
    lease_token: str
    leased_until: float
    credit_reservation: float


@dataclass(frozen=True, slots=True)
class SlotState:
    slot_id: str
    state: SlotStateValue
    leased_until: float | None
    available_at: float | None
    failure_count: int
    exhaustion_receipt_count: int


@dataclass(frozen=True, slots=True)
class PoolProjection:
    outcome: Literal[
        "available", "temporarily_unavailable", "authority_required", "budget_exhausted"
    ]
    available: int
    leased: int
    cooldown: int
    exhausted: int
    invalid: int
    earliest_availability: float | None


class TavilyPoolUnavailableError(RuntimeError):
    def __init__(self, *, earliest_availability: float | None) -> None:
        self.earliest_availability = earliest_availability
        super().__init__("Tavily key pool has no currently available lease")


ProviderContractError = EvidenceProviderContractError


@dataclass(frozen=True, slots=True)
class SecretRef:
    slot_id: str

    def __post_init__(self) -> None:
        if self.slot_id not in TAVILY_SLOT_IDS:
            raise ValueError("secret reference must identify a registered Tavily slot")


class SecretProvider(Protocol):
    def resolve(self, ref: SecretRef) -> str: ...


class _RedactingSecretProvider:
    """仅在 transport 最终发送点暴露脱敏后的 secret 解析接缝。"""

    def __init__(self, provider: SecretProvider) -> None:
        self._provider = provider

    def resolve(self, ref: SecretRef) -> str:
        try:
            value = self._provider.resolve(ref)
        except Exception:  # noqa: BLE001 - privileged provider failures stay opaque.
            raise EvidenceGatewayAuthenticationError() from None
        if type(value) is not str or not value:
            raise EvidenceGatewayAuthenticationError()
        return value


@dataclass(frozen=True, slots=True)
class TavilyTransportResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def __post_init__(self) -> None:
        if (
            type(self.status_code) is not int
            or not 100 <= self.status_code <= 599
            or type(self.headers) is not dict
            or any(
                type(key) is not str or type(value) is not str
                for key, value in self.headers.items()
            )
            or type(self.body) is not bytes
        ):
            raise ValueError(
                "transport response must use exact non-sensitive wire values"
            )


class TavilyTransport(Protocol):
    def send(
        self,
        *,
        origin: str,
        endpoint: Endpoint,
        payload: dict[str, object],
        request_id: str,
        secret_ref: SecretRef,
        secret_provider: SecretProvider,
    ) -> TavilyTransportResponse: ...


class HttpxTavilyTransport:
    """延迟加载 authoring profile 的 httpx，并在最终发送点解析 secret。"""

    def send(
        self,
        *,
        origin: str,
        endpoint: Endpoint,
        payload: dict[str, object],
        request_id: str,
        secret_ref: SecretRef,
        secret_provider: SecretProvider,
    ) -> TavilyTransportResponse:
        if origin != "https://api.tavily.com" or endpoint not in {
            "/crawl",
            "/extract",
            "/search",
        }:
            raise ProviderContractError("endpoint_not_allowed")
        httpx = importlib.import_module("httpx")
        transport_error = cast(type[Exception], httpx.TransportError)
        try:
            api_key = secret_provider.resolve(secret_ref)
        except Exception:  # noqa: BLE001 - privileged provider errors are always redacted.
            raise EvidenceGatewayAuthenticationError() from None
        if type(api_key) is not str or not api_key:
            raise EvidenceGatewayAuthenticationError()
        try:
            response = httpx.post(
                origin + endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Request-ID": request_id,
                },
                json=payload,
                timeout=cast(float, payload.get("timeout", 30.0)),
                follow_redirects=False,
            )
        except transport_error:
            raise ConnectionError("Tavily transport failed") from None
        finally:
            api_key = "[REDACTED_SECRET]"
        return TavilyTransportResponse(
            status_code=int(response.status_code),
            headers={
                str(key).lower(): str(value) for key, value in response.headers.items()
            },
            body=bytes(response.content),
        )


class EvidenceArtifactSink(Protocol):
    @property
    def raw_store_ref(self) -> EvidenceStoreRef: ...

    def put(
        self,
        artifact_type: str,
        payload: StrictFrozenModel,
        parents: tuple[ArtifactReference, ...],
    ) -> ArtifactReference: ...


class RepositoryEvidenceArtifactSink:
    def __init__(
        self,
        repository: ArtifactRepository,
        *,
        created_by: str,
        timestamp: Callable[[], str],
        raw_store_ref: EvidenceStoreRef,
    ) -> None:
        if (
            type(raw_store_ref) is not EvidenceStoreRef
            or raw_store_ref.tier != "allowed_evidence"
        ):
            raise ValueError("repository sink requires an Allowed Evidence Store")
        self._repository = repository
        self._created_by = created_by
        self._timestamp = timestamp
        self._raw_store_ref = raw_store_ref

    @property
    def raw_store_ref(self) -> EvidenceStoreRef:
        return self._raw_store_ref

    def put(
        self,
        artifact_type: str,
        payload: StrictFrozenModel,
        parents: tuple[ArtifactReference, ...],
    ) -> ArtifactReference:
        canonical_parents = tuple(
            sorted(parents, key=lambda item: item.artifact_id.encode("utf-8"))
        )
        if len({item.artifact_id for item in canonical_parents}) != len(
            canonical_parents
        ):
            raise ValueError("evidence artifact parents must be unique")
        result = self._repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": canonical_json_bytes(
                    payload.model_dump(mode="json", round_trip=True, warnings="error")
                ),
                "parent_artifact_ids": [item.artifact_id for item in canonical_parents],
                "created_by": self._created_by,
                "created_at": self._timestamp(),
                "source_evidence_ids": [],
            }
        )
        return ArtifactReference(
            artifact_id=result.artifact_id.root,
            payload_hash=result.payload_hash.root,
        )


@dataclass(frozen=True, slots=True)
class NoEvidenceRoute:
    plan: AblationExecutionPlanRef
    omission_record: EvidenceOmissionRecord
    omission_record_ref: ArtifactReference
    binding: EvidenceOmissionBinding


def create_evidence_route(
    *,
    method_id: str,
    allow_retrieval: bool,
    ablation_plan: AblationExecutionPlanRef | None,
    omission_record: EvidenceOmissionRecord | None,
    omission_record_ref: ArtifactReference | None,
    omission_binding: EvidenceOmissionBinding | None,
    gateway_factory: Callable[[], TavilyEvidenceGateway],
) -> TavilyEvidenceGateway | NoEvidenceRoute:
    """只让 exact no-evidence 合同跳过 gateway construction。"""

    if method_id == "automarkov_no_evidence":
        if (
            allow_retrieval is not False
            or type(ablation_plan) is not AblationExecutionPlanRef
            or type(omission_record) is not EvidenceOmissionRecord
            or type(omission_binding) is not EvidenceOmissionBinding
            or type(omission_record_ref) is not ArtifactReference
            or omission_binding.omission_record_ref != omission_record_ref
            or omission_record.ablation_execution_plan_ref != ablation_plan.plan_ref
            or omission_record.pair_binding_ref != ablation_plan.pair_binding_ref
        ):
            raise ProviderContractError("invalid_no_evidence_contract")
        return NoEvidenceRoute(
            plan=ablation_plan,
            omission_record=omission_record,
            omission_record_ref=omission_record_ref,
            binding=omission_binding,
        )
    if allow_retrieval is not True:
        raise ProviderContractError("retrieval_disabled_without_exact_ablation")
    if any(
        item is not None
        for item in (
            ablation_plan,
            omission_record,
            omission_record_ref,
            omission_binding,
        )
    ):
        raise ProviderContractError("omission_contract_on_retrieval_branch")
    return gateway_factory()


class SqliteTavilyKeyLeaseStore:
    """跨进程原子租用 29 个逻辑 secret slot；永不接收或存储 key 值。"""

    _SCHEMA = (
        """CREATE TABLE IF NOT EXISTS tavily_key_slots (
            slot_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('AVAILABLE','COOLDOWN','EXHAUSTED','INVALID')),
            leased_until REAL,
            lease_token TEXT,
            available_at REAL,
            failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
            exhaustion_receipt_count INTEGER NOT NULL DEFAULT 0
                CHECK (exhaustion_receipt_count >= 0),
            last_endpoint TEXT,
            last_status INTEGER,
            CHECK ((leased_until IS NULL) = (lease_token IS NULL))
        ) STRICT""",
        """CREATE TABLE IF NOT EXISTS tavily_run_cursors (
            run_id TEXT PRIMARY KEY,
            acquisition_count INTEGER NOT NULL CHECK (acquisition_count >= 0)
        ) STRICT""",
        """CREATE TABLE IF NOT EXISTS tavily_endpoint_windows (
            slot_id TEXT NOT NULL REFERENCES tavily_key_slots(slot_id),
            endpoint_group TEXT NOT NULL CHECK (endpoint_group IN ('crawl','non_crawl')),
            window_started REAL NOT NULL,
            request_count INTEGER NOT NULL CHECK (request_count >= 0),
            PRIMARY KEY (slot_id, endpoint_group)
        ) STRICT""",
        """CREATE TABLE IF NOT EXISTS tavily_pair_budgets (
            pair_binding_id TEXT PRIMARY KEY,
            logical_calls INTEGER NOT NULL DEFAULT 0 CHECK (logical_calls >= 0),
            provider_attempts INTEGER NOT NULL DEFAULT 0 CHECK (provider_attempts >= 0),
            credits_reserved REAL NOT NULL DEFAULT 0.0 CHECK (credits_reserved >= 0.0),
            credits_settled REAL NOT NULL DEFAULT 0.0 CHECK (credits_settled >= 0.0),
            ambiguous_reservation REAL NOT NULL DEFAULT 0.0
                CHECK (ambiguous_reservation >= 0.0)
        ) STRICT""",
    )

    def __init__(self, path: Path, *, server_secret: bytes) -> None:
        if not isinstance(path, Path):
            raise TypeError("lease store path must be an exact Path")
        if type(server_secret) is not bytes or len(server_secret) < 32:
            raise ValueError("lease store requires an exclusive 256-bit server secret")
        self._path = path
        self._server_secret = bytes(server_secret)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            for statement in self._SCHEMA:
                connection.execute(statement)
            connection.executemany(
                """INSERT OR IGNORE INTO tavily_key_slots(slot_id, state)
                VALUES (?, 'AVAILABLE')""",
                ((slot_id,) for slot_id in TAVILY_SLOT_IDS),
            )
            connection.commit()
        if self.registered_slot_ids() != TAVILY_SLOT_IDS:
            raise RuntimeError(
                "lease database contains an invalid Tavily slot registry"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def registered_slot_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT slot_id FROM tavily_key_slots ORDER BY slot_id"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _start_index(self, run_id: str) -> int:
        message = canonical_json_bytes(
            {
                "domain": "AutoMarkov-Tavily-Key-RoundRobin-v1",
                "run_id": run_id,
            }
        )
        digest = hmac.digest(self._server_secret, message, "sha256")
        return int.from_bytes(digest[:8], "big") % len(TAVILY_SLOT_IDS)

    def begin_logical_call(
        self,
        *,
        pair_binding_id: str,
        logical_call_ceiling: int,
    ) -> None:
        if (
            type(pair_binding_id) is not str
            or not pair_binding_id
            or type(logical_call_ceiling) is not int
            or logical_call_ceiling <= 0
        ):
            raise ValueError("logical-call budget binding is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO tavily_pair_budgets(pair_binding_id)
                VALUES (?)""",
                (pair_binding_id,),
            )
            row = connection.execute(
                "SELECT logical_calls FROM tavily_pair_budgets WHERE pair_binding_id=?",
                (pair_binding_id,),
            ).fetchone()
            if row is None or int(row[0]) >= logical_call_ceiling:
                connection.rollback()
                raise EvidenceBudgetLimitError("logical_call_ceiling_reached")
            connection.execute(
                """UPDATE tavily_pair_budgets SET logical_calls=logical_calls+1
                WHERE pair_binding_id=?""",
                (pair_binding_id,),
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def acquire(
        self,
        *,
        run_id: str,
        endpoint: Endpoint,
        now: float,
        lease_seconds: float,
        credit_reservation: float,
        requests_per_minute: int,
        pair_binding_id: str | None = None,
        credit_ceiling: int | None = None,
    ) -> KeyLease:
        if type(run_id) is not str or not run_id:
            raise ValueError("lease acquisition requires a run identity")
        if endpoint not in {"/crawl", "/extract", "/search"}:
            raise ValueError("endpoint is outside the Tavily allowlist")
        if (
            type(now) is not float
            or type(lease_seconds) is not float
            or type(credit_reservation) is not float
            or now < 0.0
            or lease_seconds <= 0.0
            or credit_reservation <= 0.0
            or type(requests_per_minute) is not int
            or requests_per_minute <= 0
        ):
            raise ValueError("lease acquisition limits must be exact positive values")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE tavily_key_slots
                SET leased_until=NULL, lease_token=NULL
                WHERE leased_until IS NOT NULL AND leased_until <= ?""",
                (now,),
            )
            connection.execute(
                """UPDATE tavily_key_slots
                SET state='AVAILABLE', available_at=NULL
                WHERE state='COOLDOWN' AND available_at <= ?""",
                (now,),
            )
            row = connection.execute(
                "SELECT acquisition_count FROM tavily_run_cursors WHERE run_id=?",
                (run_id,),
            ).fetchone()
            acquisition_count = int(row[0]) if row is not None else 0
            start = (self._start_index(run_id) + acquisition_count) % len(
                TAVILY_SLOT_IDS
            )
            endpoint_group = "crawl" if endpoint == "/crawl" else "non_crawl"
            selected: str | None = None
            for offset in range(len(TAVILY_SLOT_IDS)):
                slot_id = TAVILY_SLOT_IDS[(start + offset) % len(TAVILY_SLOT_IDS)]
                state_row = connection.execute(
                    """SELECT state, leased_until FROM tavily_key_slots
                    WHERE slot_id=?""",
                    (slot_id,),
                ).fetchone()
                if (
                    state_row is None
                    or state_row[0] != "AVAILABLE"
                    or state_row[1] is not None
                ):
                    continue
                window = connection.execute(
                    """SELECT window_started, request_count
                    FROM tavily_endpoint_windows
                    WHERE slot_id=? AND endpoint_group=?""",
                    (slot_id, endpoint_group),
                ).fetchone()
                if window is not None and now - float(window[0]) < 60.0:
                    if int(window[1]) >= requests_per_minute:
                        continue
                    connection.execute(
                        """UPDATE tavily_endpoint_windows SET request_count=request_count+1
                        WHERE slot_id=? AND endpoint_group=?""",
                        (slot_id, endpoint_group),
                    )
                else:
                    connection.execute(
                        """INSERT INTO tavily_endpoint_windows(
                            slot_id, endpoint_group, window_started, request_count
                        ) VALUES (?, ?, ?, 1)
                        ON CONFLICT(slot_id, endpoint_group) DO UPDATE SET
                            window_started=excluded.window_started,
                            request_count=excluded.request_count""",
                        (slot_id, endpoint_group, now),
                    )
                selected = slot_id
                break
            if selected is None:
                earliest_row = connection.execute(
                    """SELECT MIN(candidate) FROM (
                        SELECT available_at AS candidate FROM tavily_key_slots
                        WHERE state='COOLDOWN' AND available_at IS NOT NULL
                        UNION ALL
                        SELECT leased_until AS candidate FROM tavily_key_slots
                        WHERE leased_until IS NOT NULL
                        UNION ALL
                        SELECT window_started + 60.0 AS candidate
                        FROM tavily_endpoint_windows
                        WHERE endpoint_group=? AND request_count >= ?
                    )""",
                    (endpoint_group, requests_per_minute),
                ).fetchone()
                earliest = (
                    float(earliest_row[0])
                    if earliest_row is not None and earliest_row[0] is not None
                    else None
                )
                connection.rollback()
                raise TavilyPoolUnavailableError(earliest_availability=earliest)

            lease_token = token_hex(32)
            leased_until = now + lease_seconds
            if pair_binding_id is not None:
                if type(credit_ceiling) is not int or credit_ceiling <= 0:
                    raise ValueError("pair credit ceiling must be a positive integer")
                budget_row = connection.execute(
                    """SELECT credits_reserved FROM tavily_pair_budgets
                    WHERE pair_binding_id=?""",
                    (pair_binding_id,),
                ).fetchone()
                if budget_row is None:
                    raise ValueError("logical call must reserve its pair budget first")
                if float(budget_row[0]) + credit_reservation > credit_ceiling:
                    connection.rollback()
                    raise EvidenceBudgetLimitError("credit_ceiling_reached")
                connection.execute(
                    """UPDATE tavily_pair_budgets SET
                        provider_attempts=provider_attempts+1,
                        credits_reserved=credits_reserved+?
                    WHERE pair_binding_id=?""",
                    (credit_reservation, pair_binding_id),
                )
            connection.execute(
                """UPDATE tavily_key_slots
                SET leased_until=?, lease_token=?, last_endpoint=?
                WHERE slot_id=?""",
                (leased_until, lease_token, endpoint, selected),
            )
            connection.execute(
                """INSERT INTO tavily_run_cursors(run_id, acquisition_count)
                VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET acquisition_count=excluded.acquisition_count""",
                (run_id, acquisition_count + 1),
            )
            connection.commit()
            return KeyLease(
                slot_id=selected,
                endpoint=endpoint,
                lease_token=lease_token,
                leased_until=leased_until,
                credit_reservation=credit_reservation,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def complete(
        self,
        lease: KeyLease,
        *,
        status_code: int | None,
        now: float,
        retry_after: float | None = None,
        has_exhaustion_receipt: bool = False,
        pair_binding_id: str | None = None,
        usage_credits: float | None = None,
        ambiguous_cost: bool = False,
    ) -> None:
        if type(lease) is not KeyLease:
            raise TypeError("lease completion requires the exact issued lease")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lease_token FROM tavily_key_slots WHERE slot_id=?",
                (lease.slot_id,),
            ).fetchone()
            if row is None or row[0] != lease.lease_token:
                raise ValueError("lease token is stale or was not issued by this store")

            state: SlotStateValue = "AVAILABLE"
            available_at: float | None = None
            failure_increment = 0
            receipt_increment = 0
            if status_code == 401:
                state = "INVALID"
                failure_increment = 1
            elif status_code == 429:
                if (
                    type(retry_after) is not float
                    or not isfinite(retry_after)
                    or type(now) is not float
                    or not isfinite(now)
                    or retry_after < 0.0
                    or not isfinite(now + retry_after)
                ):
                    raise ValueError(
                        "429 completion requires an exact Retry-After delay"
                    )
                state = "COOLDOWN"
                available_at = now + retry_after
                failure_increment = 1
            elif status_code in {432, 433}:
                if has_exhaustion_receipt is True:
                    state = "EXHAUSTED"
                    receipt_increment = 1
                else:
                    state = "INVALID"
                failure_increment = 1
            elif status_code is None or status_code >= 500:
                failure_increment = 1
            connection.execute(
                """UPDATE tavily_key_slots SET
                    state=?, leased_until=NULL, lease_token=NULL, available_at=?,
                    failure_count=failure_count+?,
                    exhaustion_receipt_count=exhaustion_receipt_count+?,
                    last_status=?
                WHERE slot_id=?""",
                (
                    state,
                    available_at,
                    failure_increment,
                    receipt_increment,
                    status_code,
                    lease.slot_id,
                ),
            )
            if pair_binding_id is not None:
                budget_row = connection.execute(
                    "SELECT 1 FROM tavily_pair_budgets WHERE pair_binding_id=?",
                    (pair_binding_id,),
                ).fetchone()
                if budget_row is None:
                    raise ValueError("unknown pair budget on lease completion")
                if ambiguous_cost:
                    connection.execute(
                        """UPDATE tavily_pair_budgets SET
                            ambiguous_reservation=ambiguous_reservation+?
                        WHERE pair_binding_id=?""",
                        (lease.credit_reservation, pair_binding_id),
                    )
                elif usage_credits is not None:
                    if type(usage_credits) is not float or usage_credits < 0.0:
                        raise ValueError(
                            "settled usage must be an exact nonnegative float"
                        )
                    connection.execute(
                        """UPDATE tavily_pair_budgets SET
                            credits_settled=credits_settled+?
                        WHERE pair_binding_id=?""",
                        (usage_credits, pair_binding_id),
                    )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def slot_state(self, slot_id: str) -> SlotState:
        if slot_id not in TAVILY_SLOT_IDS:
            raise ValueError("unknown Tavily key slot")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT state, leased_until, available_at, failure_count,
                exhaustion_receipt_count FROM tavily_key_slots WHERE slot_id=?""",
                (slot_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - constructor verifies exact registry.
            raise RuntimeError("registered Tavily slot disappeared")
        return SlotState(
            slot_id=slot_id,
            state=row[0],
            leased_until=float(row[1]) if row[1] is not None else None,
            available_at=float(row[2]) if row[2] is not None else None,
            failure_count=int(row[3]),
            exhaustion_receipt_count=int(row[4]),
        )

    def project_pool(self, *, now: float) -> PoolProjection:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE tavily_key_slots
                SET leased_until=NULL, lease_token=NULL
                WHERE leased_until IS NOT NULL AND leased_until <= ?""",
                (now,),
            )
            connection.execute(
                """UPDATE tavily_key_slots SET state='AVAILABLE', available_at=NULL
                WHERE state='COOLDOWN' AND available_at <= ?""",
                (now,),
            )
            rows = connection.execute(
                """SELECT state, leased_until, available_at,
                exhaustion_receipt_count FROM tavily_key_slots"""
            ).fetchall()
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        available = sum(row[0] == "AVAILABLE" and row[1] is None for row in rows)
        leased = sum(row[1] is not None for row in rows)
        cooldown = sum(row[0] == "COOLDOWN" for row in rows)
        exhausted = sum(row[0] == "EXHAUSTED" for row in rows)
        invalid = sum(row[0] == "INVALID" for row in rows)
        candidates = [
            float(candidate)
            for row in rows
            for candidate in (row[1], row[2])
            if candidate is not None
        ]
        earliest = min(candidates) if candidates else None
        if available:
            outcome = "available"
        elif cooldown or leased:
            outcome = "temporarily_unavailable"
        elif exhausted == len(TAVILY_SLOT_IDS) and all(
            row[0] == "EXHAUSTED" and int(row[3]) > 0 for row in rows
        ):
            outcome = "budget_exhausted"
        else:
            outcome = "authority_required"
        return PoolProjection(
            outcome=outcome,
            available=available,
            leased=leased,
            cooldown=cooldown,
            exhausted=exhausted,
            invalid=invalid,
            earliest_availability=earliest,
        )


class TavilyEvidenceGateway:
    def __init__(
        self,
        *,
        budget: EvidenceBudgetManifest,
        lease_store: SqliteTavilyKeyLeaseStore,
        command_authority: CommandAuthority,
        expected_process_execution_id: str,
        evidence_view_verifier: Callable[
            [GenerationEvidenceView], GenerationEvidenceView
        ],
        transport: TavilyTransport,
        secret_provider: SecretProvider,
        clock: Callable[[], float],
        crawl_review_verifier: (
            Callable[[ArtifactReference], CrawlSafetyReview] | None
        ) = None,
        jitter: Callable[[float, float], float] | None = None,
        artifact_sink: EvidenceArtifactSink | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if type(budget) is not EvidenceBudgetManifest:
            raise TypeError("gateway budget must use the exact frozen contract")
        if type(lease_store) is not SqliteTavilyKeyLeaseStore:
            raise TypeError("gateway requires the concurrent SQLite lease store")
        if type(command_authority) is not CommandAuthority:
            raise TypeError("gateway requires a transport-owned command authority")
        if (
            type(expected_process_execution_id) is not str
            or not expected_process_execution_id
        ):
            raise ValueError("gateway requires a process execution identity")
        self._budget = budget
        self._lease_store = lease_store
        self._command_authority = command_authority
        self._expected_process_execution_id = expected_process_execution_id
        self._verify_view = evidence_view_verifier
        self._transport = transport
        self._secret_provider = _RedactingSecretProvider(secret_provider)
        self._clock = clock
        self._verify_crawl_review = crawl_review_verifier
        self._jitter = jitter or SystemRandom().uniform
        self._artifact_sink = artifact_sink
        self._sleep = sleeper or time.sleep

    def _authenticate(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
        context: AuthenticatedCommandContext,
    ) -> None:
        view = request.generation_evidence_view
        if (
            type(context) is not AuthenticatedCommandContext
            or not self._command_authority.verifies(context)
            or context.principal_id != view.principal_id
            or context.process_execution_id != self._expected_process_execution_id
        ):
            raise EvidenceGatewayAuthenticationError()
        verified = self._verify_view(view)
        if (
            type(verified) is not GenerationEvidenceView
            or verified != view
            or verified.capability_grant.principal_kind != "researcher"
            or any(store.tier != "allowed_evidence" for store in verified.stores)
            or self._artifact_sink is not None
            and self._artifact_sink.raw_store_ref not in verified.stores
        ):
            raise EvidenceGatewayAuthenticationError()

    def _validate_budget_binding(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
    ) -> None:
        if request.pair_binding_id != self._budget.pair_binding_id:
            raise ProviderContractError("pair_budget_binding_mismatch")
        if isinstance(request, SearchEvidenceRequest):
            if request.max_results > self._budget.search_max_results:
                raise ProviderContractError("search_result_ceiling_exceeded")
            if not set(request.include_domains).issubset(self._budget.allowed_domains):
                raise ProviderContractError("search_domain_not_allowed")
            if set(request.exclude_domains) != set(self._budget.blocked_domains):
                raise ProviderContractError("search_blocked_domain_policy_mismatch")
        elif isinstance(request, ExtractEvidenceRequest):
            if len(request.urls) > self._budget.extract_max_urls:
                raise ProviderContractError("extract_url_ceiling_exceeded")
            if any(not self._url_allowed(url) for url in request.urls):
                raise ProviderContractError("extract_url_not_allowed")
        else:
            self._validate_crawl_review(request)
            if (
                request.max_depth > self._budget.crawl_max_depth
                or request.max_breadth > self._budget.crawl_max_breadth
                or request.limit > self._budget.crawl_page_limit
                or not self._url_allowed(request.root_url)
            ):
                raise ProviderContractError("crawl_policy_exceeded")
            if any(
                path not in self._budget.blocked_path_prefixes
                for path in request.exclude_paths
            ):
                raise ProviderContractError("crawl_blocked_path_policy_mismatch")

    def _validate_crawl_review(self, request: CrawlEvidenceRequest) -> None:
        if self._verify_crawl_review is None:
            raise ProviderContractError("crawl_safety_review_unavailable")
        review = self._verify_crawl_review(request.safety_review_ref)
        root = urlsplit(request.root_url)
        reviewed_origin = f"{root.scheme}://{root.netloc}/"
        if (
            type(review) is not CrawlSafetyReview
            or review.reviewed_root_url != request.root_url
            or review.reviewed_origin_url != reviewed_origin
            or review.reviewed_domain != root.hostname
        ):
            raise ProviderContractError("crawl_safety_review_binding_mismatch")

    def _url_allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.hostname in self._budget.allowed_domains
            and parsed.hostname not in self._budget.blocked_domains
            and any(
                url.startswith(prefix) for prefix in self._budget.allowed_url_prefixes
            )
            and not any(
                parsed.path.startswith(prefix)
                for prefix in self._budget.blocked_path_prefixes
            )
        )

    def search(
        self,
        request: SearchEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        if type(request) is not SearchEvidenceRequest:
            raise TypeError("search requires the exact v2 request")
        return self._execute(request, context)

    def extract(
        self,
        request: ExtractEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        if type(request) is not ExtractEvidenceRequest:
            raise TypeError("extract requires the exact v1 request")
        return self._execute(request, context)

    def crawl(
        self,
        request: CrawlEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        if type(request) is not CrawlEvidenceRequest:
            raise TypeError("crawl requires the exact v2 request")
        return self._execute(request, context)

    def _execute(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        self._authenticate(request, context)
        self._validate_budget_binding(request)
        try:
            self._lease_store.begin_logical_call(
                pair_binding_id=request.pair_binding_id,
                logical_call_ceiling=self._budget.logical_call_ceiling,
            )
        except EvidenceBudgetLimitError as error:
            return self._result(
                request.request_id,
                "budget_exhausted",
                [],
                request_ref=None,
                reason_code=error.reason_code,
            )
        request_ref = self._persist_request(request)
        receipts: list[ProviderAttemptReceipt] = []
        last_transient = False
        for attempt_number in range(1, self._budget.attempt_ceiling_per_call + 1):
            now = self._clock()
            reservation = self._credit_reservation(request)
            rpm = (
                self._budget.crawl_requests_per_minute
                if request.endpoint == "/crawl"
                else self._budget.non_crawl_requests_per_minute
            )
            try:
                lease = self._lease_store.acquire(
                    run_id=request.run_id,
                    endpoint=request.endpoint,
                    now=now,
                    lease_seconds=self._budget.key_lease_wait_seconds,
                    credit_reservation=reservation,
                    requests_per_minute=rpm,
                    pair_binding_id=request.pair_binding_id,
                    credit_ceiling=self._budget.credit_ceiling,
                )
            except TavilyPoolUnavailableError as error:
                return self._pool_result(
                    request.request_id,
                    receipts,
                    request_ref=request_ref,
                    now=now,
                    forced_earliest=error.earliest_availability,
                )
            except EvidenceBudgetLimitError as error:
                return self._result(
                    request.request_id,
                    "budget_exhausted",
                    receipts,
                    request_ref=request_ref,
                    reason_code=error.reason_code,
                )

            provider_request_id = (
                f"{request.request_id}.attempt.{attempt_number}.{token_hex(8)}"
            )
            payload = self._provider_payload(request)
            started = self._clock()
            try:
                response = self._transport.send(
                    origin=self._budget.api_origin,
                    endpoint=request.endpoint,
                    payload=payload,
                    request_id=provider_request_id,
                    secret_ref=SecretRef(lease.slot_id),
                    secret_provider=self._secret_provider,
                )
            except ConnectionError:
                self._lease_store.complete(
                    lease,
                    status_code=None,
                    now=self._clock(),
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_request_id,
                        request_payload=payload,
                        response=None,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                last_transient = True
                self._backoff(attempt_number)
                continue

            status = response.status_code
            provider_response_id = (
                self._response_request_id(response) or provider_request_id
            )
            if status == 429:
                retry_after = self._retry_after(response)
                jitter_ceiling = min(
                    retry_after,
                    self._budget.retry_max_seconds,
                )
                retry_jitter = self._jitter(0.0, jitter_ceiling)
                if (
                    type(retry_jitter) is not float
                    or not 0.0 <= retry_jitter <= jitter_ceiling
                ):
                    raise ProviderContractError("retry_jitter_out_of_range")
                cooldown = retry_after + retry_jitter
                self._lease_store.complete(
                    lease,
                    status_code=429,
                    now=self._clock(),
                    retry_after=cooldown,
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_response_id,
                        request_payload=payload,
                        response=response,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                last_transient = True
                continue
            if status in {401, 432, 433}:
                has_exhaustion_receipt = status in {
                    432,
                    433,
                } and self._response_has_usage(response)
                self._lease_store.complete(
                    lease,
                    status_code=status,
                    now=self._clock(),
                    has_exhaustion_receipt=has_exhaustion_receipt,
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_response_id,
                        request_payload=payload,
                        response=response,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                continue
            if status == 403:
                self._lease_store.complete(
                    lease,
                    status_code=403,
                    now=self._clock(),
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_response_id,
                        request_payload=payload,
                        response=response,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                return self._result(
                    request.request_id,
                    "blocked",
                    receipts,
                    request_ref=request_ref,
                    reason_code="provider_permission_denied",
                )
            if status >= 500:
                self._lease_store.complete(
                    lease,
                    status_code=status,
                    now=self._clock(),
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_response_id,
                        request_payload=payload,
                        response=response,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                last_transient = True
                self._backoff(attempt_number)
                continue
            if status != 200:
                self._lease_store.complete(
                    lease,
                    status_code=status,
                    now=self._clock(),
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_response_id,
                        request_payload=payload,
                        response=response,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                return self._result(
                    request.request_id,
                    "blocked",
                    receipts,
                    request_ref=request_ref,
                    reason_code="unexpected_provider_status",
                )
            try:
                response_payload, usage = self._validated_provider_payload(response)
                snapshot = self._snapshot(request, response_payload)
            except (ValueError, ProviderContractError):
                self._lease_store.complete(
                    lease,
                    status_code=500,
                    now=self._clock(),
                    pair_binding_id=request.pair_binding_id,
                    ambiguous_cost=True,
                )
                receipts.append(
                    self._receipt(
                        lease=lease,
                        attempt_number=attempt_number,
                        provider_request_id=provider_response_id,
                        request_payload=payload,
                        response=response,
                        started=started,
                        usage=None,
                        cost_state="ambiguous",
                    )
                )
                return self._result(
                    request.request_id,
                    "blocked",
                    receipts,
                    request_ref=request_ref,
                    reason_code="provider_contract_violation",
                )
            self._lease_store.complete(
                lease,
                status_code=200,
                now=self._clock(),
                pair_binding_id=request.pair_binding_id,
                usage_credits=usage,
            )
            receipts.append(
                self._receipt(
                    lease=lease,
                    attempt_number=attempt_number,
                    provider_request_id=provider_response_id,
                    request_payload=payload,
                    response=response,
                    started=started,
                    usage=usage,
                    cost_state="settled",
                )
            )
            (
                attempt_refs,
                raw_document_refs,
                snapshot_ref,
                ledger_revision_ref,
            ) = self._persist_success(request_ref, receipts, snapshot)
            return EvidenceGatewayResult.model_validate(
                {
                    "schema_version": "automarkov.evidence-gateway-result.v1",
                    "outcome": "available",
                    "request_id": request.request_id,
                    "snapshot": snapshot.model_dump(mode="python", warnings="error"),
                    "attempt_receipts": [
                        item.model_dump(mode="python", warnings="error")
                        for item in receipts
                    ],
                    "request_ref": (
                        request_ref.model_dump(mode="python")
                        if request_ref is not None
                        else None
                    ),
                    "attempt_receipt_refs": [
                        item.model_dump(mode="python") for item in attempt_refs
                    ],
                    "raw_document_refs": [
                        item.model_dump(mode="python") for item in raw_document_refs
                    ],
                    "snapshot_ref": (
                        snapshot_ref.model_dump(mode="python")
                        if snapshot_ref is not None
                        else None
                    ),
                    "ledger_revision_ref": (
                        ledger_revision_ref.model_dump(mode="python")
                        if ledger_revision_ref is not None
                        else None
                    ),
                    "earliest_availability": None,
                    "reason_code": None,
                },
                strict=True,
            )
        outcome = "temporarily_unavailable" if last_transient else "blocked"
        return self._result(
            request.request_id,
            outcome,
            receipts,
            request_ref=request_ref,
            reason_code="attempt_budget_exhausted",
            earliest=self._clock() + self._budget.retry_max_seconds
            if last_transient
            else None,
        )

    def _credit_reservation(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
    ) -> float:
        if isinstance(request, SearchEvidenceRequest):
            return 2.0 if request.search_depth == "advanced" else 1.0
        if isinstance(request, ExtractEvidenceRequest):
            multiplier = 2.0 if request.extract_depth == "advanced" else 1.0
            return multiplier * max(1.0, (len(request.urls) + 4) // 5)
        return 2.0

    def _provider_payload(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
    ) -> dict[str, object]:
        common: dict[str, object] = {"include_usage": True, "include_images": False}
        if isinstance(request, SearchEvidenceRequest):
            return {
                "query": request.query,
                "include_answer": False,
                "include_usage": True,
                "include_raw_content": False,
                "include_images": False,
                "auto_parameters": False,
                "search_depth": request.search_depth,
                "max_results": request.max_results,
                "include_domains": list(request.include_domains),
                "exclude_domains": list(request.exclude_domains),
            }
        if isinstance(request, ExtractEvidenceRequest):
            return {
                **common,
                "urls": list(request.urls),
                "extract_depth": request.extract_depth,
                "format": request.format,
                "timeout": request.timeout_seconds,
            }
        return {
            **common,
            "url": request.root_url,
            "allow_external": False,
            "max_depth": request.max_depth,
            "max_breadth": request.max_breadth,
            "limit": request.limit,
            "timeout": request.timeout_seconds,
            "select_paths": list(request.select_paths),
            "exclude_paths": list(request.exclude_paths),
        }

    def _validated_provider_payload(
        self, response: TavilyTransportResponse
    ) -> tuple[dict[str, object], float]:
        parsed = parse_json_payload(response.body)
        if type(parsed) is not dict:
            raise ProviderContractError("response_not_object")
        payload = cast(dict[str, object], parsed)
        answer = payload.get("answer")
        if answer not in {None, ""}:
            raise ProviderContractError("hosted_answer_present")
        usage = payload.get("usage")
        if type(usage) is not dict:
            raise ProviderContractError("usage_missing")
        credits = cast(dict[str, object], usage).get("credits")
        if type(credits) not in {int, float}:
            raise ProviderContractError("usage_credits_invalid")
        numeric_credits = cast(int | float, credits)
        if numeric_credits < 0:
            raise ProviderContractError("usage_credits_invalid")
        return payload, float(numeric_credits)

    def _snapshot(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
        payload: dict[str, object],
    ) -> SearchSnapshot | ExtractSnapshot | CrawlSnapshot:
        results = payload.get("results")
        if type(results) is not list:
            raise ProviderContractError("results_missing")
        result_items = cast(list[object], results)
        if isinstance(request, SearchEvidenceRequest):
            discoveries: list[dict[str, object]] = []
            for item in result_items:
                if type(item) is not dict:
                    raise ProviderContractError("search_result_invalid")
                result = cast(dict[str, object], item)
                discoveries.append(
                    {
                        "title": result.get("title"),
                        "url": result.get("url"),
                        "snippet": result.get("content", ""),
                    }
                )
            return SearchSnapshot.model_validate(
                {
                    "schema_version": "automarkov.search-snapshot.v1",
                    "snapshot_kind": "discovery_only",
                    "request_id": request.request_id,
                    "discoveries": discoveries,
                },
                strict=True,
            )
        documents = [self._raw_document(item) for item in result_items]
        if isinstance(request, ExtractEvidenceRequest):
            raw_failed = payload.get("failed_results", [])
            if type(raw_failed) is not list:
                raise ProviderContractError("failed_results_invalid")
            failed_urls: list[str] = []
            for item in cast(list[object], raw_failed):
                if type(item) is str:
                    failed_urls.append(item)
                elif (
                    type(item) is dict
                    and type(cast(dict[str, object], item).get("url")) is str
                ):
                    failed_urls.append(cast(str, cast(dict[str, object], item)["url"]))
                else:
                    raise ProviderContractError("failed_result_invalid")
            return ExtractSnapshot.model_validate(
                {
                    "schema_version": "automarkov.extract-snapshot.v1",
                    "request_id": request.request_id,
                    "documents": [item.model_dump(mode="python") for item in documents],
                    "failed_urls": sorted(
                        set(failed_urls), key=lambda item: item.encode("utf-8")
                    ),
                },
                strict=True,
            )
        return CrawlSnapshot.model_validate(
            {
                "schema_version": "automarkov.crawl-snapshot.v1",
                "request_id": request.request_id,
                "root_url": request.root_url,
                "documents": [item.model_dump(mode="python") for item in documents],
            },
            strict=True,
        )

    def _raw_document(self, item: object) -> RawEvidenceDocument:
        if type(item) is not dict:
            raise ProviderContractError("raw_document_invalid")
        result = cast(dict[str, object], item)
        url = result.get("url")
        content = result.get("raw_content", result.get("content"))
        if type(url) is not str or type(content) is not str or not content:
            raise ProviderContractError("raw_document_invalid")
        return RawEvidenceDocument.model_validate(
            {
                "schema_version": "automarkov.raw-evidence-document.v1",
                "source_url": url,
                "content": content,
                "content_hash": f"sha256:{sha256(content.encode('utf-8')).hexdigest()}",
                "store_tier": "allowed_evidence",
            },
            strict=True,
        )

    def _response_request_id(self, response: TavilyTransportResponse) -> str | None:
        header = response.headers.get("x-request-id")
        if type(header) is str and header:
            return header
        try:
            payload = parse_json_payload(response.body)
        except ValueError:
            return None
        if type(payload) is dict:
            value = cast(dict[str, object], payload).get("request_id")
            return value if type(value) is str and value else None
        return None

    def _response_has_usage(self, response: TavilyTransportResponse) -> bool:
        try:
            payload = parse_json_payload(response.body)
        except ValueError:
            return False
        if type(payload) is not dict:
            return False
        usage = cast(dict[str, object], payload).get("usage")
        if type(usage) is not dict:
            return False
        credits = cast(dict[str, object], usage).get("credits")
        return (
            type(credits) in {int, float}
            and isfinite(cast(int | float, credits))
            and cast(int | float, credits) >= 0
        )

    def _retry_after(self, response: TavilyTransportResponse) -> float:
        value = response.headers.get("retry-after")
        if value is None:
            return self._budget.retry_base_seconds
        try:
            retry_after = float(value)
        except ValueError as error:
            raise ProviderContractError("retry_after_invalid") from error
        if not isfinite(retry_after) or retry_after < 0.0:
            raise ProviderContractError("retry_after_invalid")
        return retry_after

    def _backoff(self, attempt_number: int) -> None:
        ceiling = min(
            self._budget.retry_max_seconds,
            self._budget.retry_base_seconds * (2 ** (attempt_number - 1)),
        )
        delay = self._jitter(0.0, ceiling)
        if type(delay) is not float or not 0.0 <= delay <= ceiling:
            raise ProviderContractError("retry_jitter_out_of_range")
        self._sleep(delay)

    def _receipt(
        self,
        *,
        lease: KeyLease,
        attempt_number: int,
        provider_request_id: str,
        request_payload: dict[str, object],
        response: TavilyTransportResponse | None,
        started: float,
        usage: float | None,
        cost_state: Literal["settled", "ambiguous"],
    ) -> ProviderAttemptReceipt:
        duration_ms = max(0, int((self._clock() - started) * 1000.0))
        return ProviderAttemptReceipt.model_validate(
            {
                "schema_version": "automarkov.provider-attempt-receipt.v1",
                "slot_id": lease.slot_id,
                "endpoint": lease.endpoint,
                "attempt_number": attempt_number,
                "http_status": response.status_code if response is not None else None,
                "provider_request_id": provider_request_id,
                "request_hash": f"sha256:{sha256(canonical_json_bytes(request_payload)).hexdigest()}",
                "response_hash": (
                    f"sha256:{sha256(response.body).hexdigest()}"
                    if response is not None
                    else None
                ),
                "duration_ms": duration_ms,
                "usage_credits": usage,
                "credit_reservation": lease.credit_reservation,
                "cost_state": cost_state,
            },
            strict=True,
        )

    def _persist_request(
        self,
        request: SearchEvidenceRequest | ExtractEvidenceRequest | CrawlEvidenceRequest,
    ) -> ArtifactReference | None:
        if self._artifact_sink is None:
            return None
        artifact_type = {
            SearchEvidenceRequest: "tavily_search_request",
            ExtractEvidenceRequest: "tavily_extract_request",
            CrawlEvidenceRequest: "tavily_crawl_request",
        }[type(request)]
        return self._artifact_sink.put(
            artifact_type,
            request,
            (
                request.task_input_ref,
                request.budget_ref,
                request.lease_pool_ref,
                *(
                    (request.safety_review_ref,)
                    if isinstance(request, CrawlEvidenceRequest)
                    else ()
                ),
            ),
        )

    def _persist_attempts(
        self,
        request_ref: ArtifactReference | None,
        receipts: list[ProviderAttemptReceipt],
    ) -> tuple[ArtifactReference, ...]:
        if self._artifact_sink is None or request_ref is None:
            return ()
        return tuple(
            self._artifact_sink.put(
                "provider_attempt_receipt",
                ProviderAttemptArtifact(
                    schema_version="automarkov.provider-attempt-artifact.v1",
                    request_ref=request_ref,
                    receipt=receipt,
                ),
                (request_ref,),
            )
            for receipt in receipts
        )

    def _persist_success(
        self,
        request_ref: ArtifactReference | None,
        receipts: list[ProviderAttemptReceipt],
        snapshot: SearchSnapshot | ExtractSnapshot | CrawlSnapshot,
    ) -> tuple[
        tuple[ArtifactReference, ...],
        tuple[ArtifactReference, ...],
        ArtifactReference | None,
        ArtifactReference | None,
    ]:
        attempt_refs = self._persist_attempts(request_ref, receipts)
        if self._artifact_sink is None or request_ref is None:
            return attempt_refs, (), None, None
        if not attempt_refs:  # pragma: no cover - success always has an attempt.
            raise RuntimeError("available evidence is missing provider-attempt lineage")
        documents = (
            () if isinstance(snapshot, SearchSnapshot) else tuple(snapshot.documents)
        )
        raw_refs = tuple(
            self._artifact_sink.put(
                "raw_evidence_document",
                RawEvidenceDocumentArtifact(
                    schema_version="automarkov.raw-evidence-document-artifact.v1",
                    attempt_ref=attempt_refs[-1],
                    allowed_store=self._artifact_sink.raw_store_ref,
                    document=document,
                ),
                (attempt_refs[-1],),
            )
            for document in documents
        )
        snapshot_payload = EvidenceSnapshotArtifact(
            schema_version="automarkov.evidence-snapshot-artifact.v1",
            request_ref=request_ref,
            attempt_refs=attempt_refs,
            raw_document_refs=raw_refs,
            snapshot=snapshot,
        )
        snapshot_ref = self._artifact_sink.put(
            "evidence_snapshot",
            snapshot_payload,
            (request_ref, *attempt_refs, *raw_refs),
        )
        ledger_payload = EvidenceLedgerRevision(
            schema_version="automarkov.evidence-ledger-revision.v1",
            request_ref=request_ref,
            snapshot_ref=snapshot_ref,
            revision_number=1,
            evidence_item_refs=raw_refs,
        )
        ledger_ref = self._artifact_sink.put(
            "evidence_ledger",
            ledger_payload,
            (request_ref, snapshot_ref, *raw_refs),
        )
        return attempt_refs, raw_refs, snapshot_ref, ledger_ref

    def _pool_result(
        self,
        request_id: str,
        receipts: list[ProviderAttemptReceipt],
        *,
        request_ref: ArtifactReference | None,
        now: float,
        forced_earliest: float | None = None,
    ) -> EvidenceGatewayResult:
        projection = self._lease_store.project_pool(now=now)
        outcome = (
            "temporarily_unavailable"
            if forced_earliest is not None
            else projection.outcome
        )
        earliest = forced_earliest or projection.earliest_availability
        return self._result(
            request_id,
            outcome,
            receipts,
            request_ref=request_ref,
            reason_code=f"tavily_pool_{outcome}",
            earliest=earliest,
        )

    def _result(
        self,
        request_id: str,
        outcome: str,
        receipts: list[ProviderAttemptReceipt],
        *,
        request_ref: ArtifactReference | None,
        reason_code: str,
        earliest: float | None = None,
    ) -> EvidenceGatewayResult:
        timestamp = (
            datetime.fromtimestamp(earliest, tz=UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
            if earliest is not None
            else None
        )
        attempt_refs = self._persist_attempts(request_ref, receipts)
        return EvidenceGatewayResult.model_validate(
            {
                "schema_version": "automarkov.evidence-gateway-result.v1",
                "outcome": outcome,
                "request_id": request_id,
                "snapshot": None,
                "attempt_receipts": [
                    item.model_dump(mode="python", warnings="error")
                    for item in receipts
                ],
                "request_ref": (
                    request_ref.model_dump(mode="python")
                    if request_ref is not None
                    else None
                ),
                "attempt_receipt_refs": [
                    item.model_dump(mode="python") for item in attempt_refs
                ],
                "raw_document_refs": [],
                "snapshot_ref": None,
                "ledger_revision_ref": None,
                "earliest_availability": timestamp,
                "reason_code": reason_code,
            },
            strict=True,
        )


__all__ = [
    "KeyLease",
    "PoolProjection",
    "SlotState",
    "SqliteTavilyKeyLeaseStore",
    "TavilyPoolUnavailableError",
]
