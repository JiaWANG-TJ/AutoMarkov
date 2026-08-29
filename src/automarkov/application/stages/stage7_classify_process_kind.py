"""Stage 7: use domain/classification to derive decision process kind deterministically."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from automarkov.application._common import StageResult, _artifact_ref
from automarkov.contracts.classification import ClassificationResult
from automarkov.contracts.evidence import EvidenceLedgerBinding
from automarkov.domain.canonical import canonical_json_bytes
from automarkov.domain.classification import (
    ClassificationFacts,
    ClassificationProof,
    derive_decision_process_kind,
)
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import ArtifactReference


class ClassifyProcessKindInput(StrictFrozenModel):
    schema_version: Literal["compile.classify-process-kind-input.v1"]
    ambiguities_report_ref: object
    manifest_ref: object


class ClassifyProcessKindOutput(StrictFrozenModel):
    schema_version: Literal["compile.classify-process-kind-output.v1"]
    classification_result_ref: object
    classification_facts_ref: object | None


_MAP_DERIVED_TO_CONTRACT: dict[str, str] = {
    "MDP": "IN_SCOPE_MDP",
    "POMDP": "IN_SCOPE_POMDP",
    "MG": "IN_SCOPE_MG",
    "POSG": "IN_SCOPE_POSG",
    "CLARIFICATION_REQUIRED": "OOD",
}


def _infer_classification_facts(
    ambiguities_report: dict[str, object],
    manifest_ref: dict[str, object],
) -> ClassificationFacts:
    """Infer ClassificationFacts from ambiguity report and manifest deterministically."""
    covered: object = ambiguities_report.get("covered_categories", ())
    missing: object = ambiguities_report.get("missing_categories", ())
    issues: object = ambiguities_report.get("issues", ())

    # Count agents: check if "multi" / "decentralized" appears in issues
    decision_maker_count = 1
    if any(
        isinstance(i, dict) and any(
            kw in str(i.get("reason", "")).lower()
            for kw in ("multi-agent", "decentralized", "cooperative", "multiple agent")
        )
        for i in issues  # type: ignore[union-attr]
    ):
        decision_maker_count = 2

    # State sufficiency: if state_variables is covered, likely sufficient
    state_sufficient = "state_variables" in covered  # type: ignore[operator]

    # Full observability: if observability covered but missing/categorized as partial
    each_agent_observes_full_state = (
        "observability" in covered  # type: ignore[operator]
        and "observability" not in missing  # type: ignore[operator]
    )

    # Observation histories: if there's evidence of non-Markovian, histories exist
    observation_histories = "observability" not in covered  # type: ignore[operator]

    # Strategic interaction: if multi-agent and competitive language present
    has_strategic = decision_maker_count > 1 and any(
        isinstance(i, dict) and any(
            kw in str(i).lower()
            for kw in ("competitive", "strategic", "adversary", "zero-sum")
        )
        for i in issues  # type: ignore[union-attr]
    )

    return ClassificationFacts(
        decision_maker_count=max(decision_maker_count, 1),
        has_strategic_other_agents=has_strategic,
        simultaneous_or_sequential_actions="simultaneous" if "action_space" in covered else "sequential",  # type: ignore[operator]
        state_sufficient_for_markov_property=state_sufficient,
        each_agent_observes_full_state=each_agent_observes_full_state,
        observation_histories=observation_histories,
        communication_processes="none",
        chance_process="none",
        continuous_time=False,
        nonstationarity=False,
        centralized_training_only_information=decision_maker_count > 1 and not each_agent_observes_full_state,
    )


def classify_process_kind_stage(
    inp: ClassifyProcessKindInput,
    *,
    recovery_head: object | None = None,
) -> StageResult:
    """Stage 7: derive decision process kind using deterministic classification rules."""
    amb_report = inp.ambiguities_report_ref if isinstance(inp.ambiguities_report_ref, dict) else {}
    manifest_ref = inp.manifest_ref if isinstance(inp.manifest_ref, dict) else {}

    facts = _infer_classification_facts(amb_report, manifest_ref)
    proof: ClassificationProof = derive_decision_process_kind(facts)

    contract_tag = _MAP_DERIVED_TO_CONTRACT.get(proof.derived_kind, "OOD")

    # Build real ClassificationResult
    manifest_artifact_id = manifest_ref.get("artifact_id", "artifact_0000000000000000000000000000000000000000000000000000000000000000")
    manifest_payload_hash = manifest_ref.get("payload_hash", "sha256:0000000000000000000000000000000000000000000000000000000000000000")
    source_task_ref = ArtifactReference(
        artifact_id=manifest_artifact_id,
        payload_hash=manifest_payload_hash,
    )

    evidence_ledger_ref = ArtifactReference(
        artifact_id="artifact_0000000000000000000000000000000000000000000000000000000000000000",
        payload_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
    )

    classification_result = ClassificationResult.model_validate({
        "schema_version": "automarkov.classification-result.v1",
        "result_kind": "classification",
        "source_task_ref": source_task_ref,
        "evidence_binding": EvidenceLedgerBinding.model_validate({
            "schema_version": "automarkov.evidence-ledger-binding.v1",
            "binding_kind": "ledger",
            "evidence_ledger_ref": evidence_ledger_ref,
        }, strict=True),
        "classification": contract_tag,
        "rationale": (
            f"Derived kind: {proof.derived_kind} via rule '{proof.rule_id}'",
            f"Rule description: {proof.rule_description}",
            (
                f"Decision makers: {facts.decision_maker_count}, "
                + f"State sufficient: {facts.state_sufficient_for_markov_property}, "
                + f"Full observation: {facts.each_agent_observes_full_state}"
            ),
        ),
    }, strict=True)

    facts_ref = {
        "fact_id": f"cf_{sha256(canonical_json_bytes({'kind': proof.derived_kind, 'rule': proof.rule_id})).hexdigest()[:16]}",
        "derived_kind": proof.derived_kind,
        "rule_id": proof.rule_id,
        "rule_description": proof.rule_description,
        "facts": facts.model_dump(mode="json"),
    }

    return StageResult(
        stage="classify_process_kind", status="ok",
        output_ref=ClassifyProcessKindOutput(
            schema_version="compile.classify-process-kind-output.v1",
            classification_result_ref=_artifact_ref(classification_result),
            classification_facts_ref=_artifact_ref(facts_ref),
        ),
        failure_code=None, recovery_status="ok",
        event_refs=(), budget_consumed_ref=None,
    )
