"""Stage 3: build structured retrieval plan from task unknowns."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal, cast

from automarkov.application._common import StageResult, _artifact_ref
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.models import StrictFrozenModel, TaskRequest


class PlanEvidenceInput(StrictFrozenModel):
    schema_version: Literal["compile.plan-evidence-input.v1"]
    validated_request: TaskRequest
    manifest_ref: object


class PlanEvidenceOutput(StrictFrozenModel):
    schema_version: Literal["compile.plan-evidence-output.v1"]
    retrieval_plan_ref: object


_SOURCE_POLICY = {
    "prefer_official_docs": True,
    "allowed_domains": ("gymnasium.farama.org", "pettingzoo.farama.org",
                        "openspiel.readthedocs.io", "arxiv.org",
                        "github.com", "pytorch.org", "tensorflow.org"),
    "prefer_recency_days": 730,
}


def _extract_unknowns(task_text: str) -> list[dict[str, object]]:
    """Extract structured unknowns from task text using keyword heuristics."""
    unknowns: list[dict[str, object]] = []
    lowered = task_text.lower()
    heuristics: list[tuple[str, str, str]] = [
        ("state", "What are the state variables?", "state_variables"),
        ("action", "What actions are available?", "action_space"),
        ("reward", "What is the reward structure?", "reward_function"),
        ("agent", "How many agents and what are their roles?", "agent_structure"),
        ("observation", "What can each agent observe?", "observation_space"),
        ("transition", "How do state transitions work?", "transition_dynamics"),
        ("horizon", "What is the time horizon?", "horizon"),
        ("constraint", "What constraints apply?", "constraints"),
        ("stochastic", "What randomness or uncertainty exists?", "stochasticity"),
        ("communication", "Do agents communicate with each other?", "communication"),
    ]
    for keyword, question, category in heuristics:
        if keyword in lowered:
            unknowns.append({
                "category": category,
                "question": question,
                "extracted_from_keyword": keyword,
                "resolved": False,
            })
    if not unknowns:
        unknowns.append({
            "category": "general",
            "question": "What decision process does this task describe?",
            "extracted_from_keyword": "fallback",
            "resolved": False,
        })
    return unknowns


def _make_queries(task_text: str, unknowns: list[dict[str, object]]) -> tuple[str, ...]:
    """Generate deterministic retrieval queries from unknowns."""
    keywords = sorted({cast(str, u["extracted_from_keyword"]) for u in unknowns if isinstance(u.get("extracted_from_keyword"), str)})
    plain_words = [w for w in task_text[:300].split()[:15] if w.isalpha() and len(w) > 2]
    base_query = " ".join(plain_words[:5]) if plain_words else "decision process specification"
    queries = [f"{base_query} {kw}" for kw in keywords[:8]]
    if not queries:
        queries = [base_query]
    return tuple(queries)


def plan_evidence_stage(
    inp: PlanEvidenceInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 3: extract structured unknowns, generate retrieval queries with budget stop rules."""
    task = inp.validated_request
    unknowns = _extract_unknowns(task.task_text)
    queries = _make_queries(task.task_text, unknowns)

    plan = {
        "schema_version": "automarkov.retrieval-plan.v1",
        "plan_id": f"plan_{sha256(canonical_json_bytes({'q': list(queries)})).hexdigest()[:16]}",
        "request_id": task.request_id,
        "unknowns": tuple(unknowns),
        "search_keywords": tuple(sorted(set(task.task_text[:200].split()[:10]))),
        "retrieval_queries": queries,
        "max_results_per_query": 5,
        "source_policy": _SOURCE_POLICY,
        "budget_stop_rules": {
            "max_total_results": 25,
            "max_tokens_consumed": 50_000,
            "stop_on_sufficient_coverage": True,
        },
    }

    return StageResult(
        stage="plan_evidence", status="ok",
        output_ref=PlanEvidenceOutput(
            schema_version="compile.plan-evidence-output.v1",
            retrieval_plan_ref=_artifact_ref(plan),
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
