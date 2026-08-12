from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from automarkov.evidence_contracts import TAVILY_SLOT_IDS
from automarkov.tavily_gateway import (
    EvidenceBudgetLimitError,
    SqliteTavilyKeyLeaseStore,
)


def test_sqlite_store_atomically_leases_only_the_exact_29_slots(tmp_path) -> None:
    path = tmp_path / "tavily-leases.sqlite3"
    store = SqliteTavilyKeyLeaseStore(path, server_secret=b"s" * 32)

    def lease(index: int) -> str:
        return store.acquire(
            run_id=f"run_{index}",
            endpoint="/search",
            now=100.0,
            lease_seconds=30.0,
            credit_reservation=1.0,
            requests_per_minute=30,
        ).slot_id

    with ThreadPoolExecutor(max_workers=12) as pool:
        leased = tuple(pool.map(lease, range(12)))

    assert len(set(leased)) == 12
    assert set(leased).issubset(TAVILY_SLOT_IDS)
    assert store.registered_slot_ids() == TAVILY_SLOT_IDS

    with sqlite3.connect(path) as connection:
        schema = " ".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL"
            )
        ).lower()
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(tavily_key_slots)")
        )
    assert "hmac" not in schema
    assert "fingerprint" not in schema
    assert "key_value" not in columns


def test_status_transitions_distinguish_cooldown_authority_and_exhaustion(
    tmp_path,
) -> None:
    store = SqliteTavilyKeyLeaseStore(
        tmp_path / "tavily-leases.sqlite3", server_secret=b"s" * 32
    )

    cooldown = store.acquire(
        run_id="run_cooldown",
        endpoint="/search",
        now=100.0,
        lease_seconds=30.0,
        credit_reservation=1.0,
        requests_per_minute=30,
    )
    store.complete(cooldown, status_code=429, now=101.0, retry_after=9.0)
    assert store.slot_state(cooldown.slot_id).state == "COOLDOWN"
    assert store.slot_state(cooldown.slot_id).available_at == 110.0

    invalid = store.acquire(
        run_id="run_invalid",
        endpoint="/search",
        now=101.0,
        lease_seconds=30.0,
        credit_reservation=1.0,
        requests_per_minute=30,
    )
    store.complete(invalid, status_code=401, now=102.0)
    assert store.slot_state(invalid.slot_id).state == "INVALID"

    exhausted = store.acquire(
        run_id="run_exhausted",
        endpoint="/search",
        now=102.0,
        lease_seconds=30.0,
        credit_reservation=1.0,
        requests_per_minute=30,
    )
    store.complete(
        exhausted,
        status_code=432,
        now=103.0,
        has_exhaustion_receipt=True,
    )
    assert store.slot_state(exhausted.slot_id).state == "EXHAUSTED"
    assert store.slot_state(exhausted.slot_id).exhaustion_receipt_count == 1

    forbidden = store.acquire(
        run_id="run_forbidden",
        endpoint="/search",
        now=103.0,
        lease_seconds=30.0,
        credit_reservation=1.0,
        requests_per_minute=30,
    )
    store.complete(forbidden, status_code=403, now=104.0)
    forbidden_state = store.slot_state(forbidden.slot_id)
    assert forbidden_state.state == "AVAILABLE"
    assert forbidden_state.leased_until is None


def test_only_29_receipted_exhaustions_project_budget_exhausted(tmp_path) -> None:
    store = SqliteTavilyKeyLeaseStore(
        tmp_path / "exhausted.sqlite3", server_secret=b"s" * 32
    )
    for index in range(29):
        lease = store.acquire(
            run_id="run_all_exhausted",
            endpoint="/search",
            now=100.0 + index,
            lease_seconds=30.0,
            credit_reservation=1.0,
            requests_per_minute=30,
        )
        store.complete(
            lease,
            status_code=433,
            now=100.5 + index,
            has_exhaustion_receipt=True,
        )
    assert store.project_pool(now=200.0).outcome == "budget_exhausted"

    mixed = SqliteTavilyKeyLeaseStore(
        tmp_path / "mixed.sqlite3", server_secret=b"s" * 32
    )
    for index in range(29):
        lease = mixed.acquire(
            run_id="run_mixed",
            endpoint="/search",
            now=100.0 + index,
            lease_seconds=30.0,
            credit_reservation=1.0,
            requests_per_minute=30,
        )
        mixed.complete(
            lease,
            status_code=401 if index == 0 else 432,
            now=100.5 + index,
            has_exhaustion_receipt=index != 0,
        )
    projection = mixed.project_pool(now=200.0)
    assert projection.outcome == "authority_required"
    assert projection.invalid == 1
    assert projection.exhausted == 28


def test_pair_shared_logical_and_credit_budgets_are_atomic(tmp_path) -> None:
    store = SqliteTavilyKeyLeaseStore(
        tmp_path / "budgets.sqlite3", server_secret=b"s" * 32
    )
    store.begin_logical_call(pair_binding_id="pair_001", logical_call_ceiling=1)
    with pytest.raises(EvidenceBudgetLimitError, match="logical_call_ceiling"):
        store.begin_logical_call(pair_binding_id="pair_001", logical_call_ceiling=1)

    with pytest.raises(EvidenceBudgetLimitError, match="credit_ceiling"):
        store.acquire(
            run_id="run_budget",
            endpoint="/search",
            now=100.0,
            lease_seconds=30.0,
            credit_reservation=2.0,
            requests_per_minute=30,
            pair_binding_id="pair_001",
            credit_ceiling=1,
        )
