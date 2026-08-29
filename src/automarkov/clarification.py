from __future__ import annotations

import base64
import sqlite3
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, Self, TypeAlias, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AfterValidator, Field, field_validator, model_validator

from automarkov.domain.canonical import FrozenSequence, canonical_json_bytes
from automarkov.domain.ids import (
    ArtifactIdValue,
    CanonicalTimestamp,
    EventId,
    NonEmptyId,
    PrincipalIdValue,
    RequestIdValue,
    RunIdValue,
    Sha256Value,
)
from automarkov.domain.models import ArtifactId, CanonicalNonce, NonNegativeSafeInt, StrictFrozenModel
from automarkov.lifecycle import TerminalResult
from automarkov.public import ArtifactRepository, ArtifactType


class ClarificationGap(StrictFrozenModel):
    target_path: str
    question: str
    consequence: str
    evidence_ids: FrozenSequence[str]

    @model_validator(mode="after")
    def require_closed_gap(self) -> Self:
        if any(
            not value.strip()
            for value in (self.target_path, self.question, self.consequence)
        ) or any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError("clarification gaps require nonblank fields")
        return self


def clarification_gap_id(gap: ClarificationGap) -> str:
    payload = gap.model_dump(mode="json", round_trip=True, warnings="error")
    return (
        "sha256:"
        + sha256(
            canonical_json_bytes(
                {
                    "domain": "AutoMarkov-Clarification-Gap-v1",
                    "gap": payload,
                }
            )
        ).hexdigest()
    )


class ClarificationRequiredResult(StrictFrozenModel):
    schema_version: Literal["automarkov.clarification-required-result.v1"]
    result_kind: Literal["clarification_required"]
    task_artifact_id: ArtifactIdValue
    review_report_artifact_id: ArtifactIdValue
    identified_gaps: FrozenSequence[ClarificationGap]
    introduced_assumptions: FrozenSequence[str]
    formal_artifact_ids: FrozenSequence[ArtifactIdValue]
    environment_artifact_ids: FrozenSequence[ArtifactIdValue]

    @model_validator(mode="after")
    def require_terminal_abstention(self) -> Self:
        gap_keys = tuple(
            (gap.target_path, gap.question) for gap in self.identified_gaps
        )
        if not gap_keys:
            raise ValueError("clarification results require at least one gap")
        if len(gap_keys) != len(set(gap_keys)):
            raise ValueError("clarification gaps must be unique")
        if (
            self.introduced_assumptions
            or self.formal_artifact_ids
            or self.environment_artifact_ids
        ):
            raise ValueError("clarification results must not guess or formalize")
        return self


class ExperimentClarificationRequiredResult(StrictFrozenModel):
    schema_version: Literal["automarkov.experiment-clarification-required-result.v1"]
    result_kind: Literal["experiment_clarification_required"]
    clarification: ClarificationRequiredResult
    outcome_mask_id: ArtifactIdValue
    variant_id: Literal["v5_clarification_required"]
    track: Literal["AUTO"]


class TerminalArtifactDagEntry(StrictFrozenModel):
    artifact_id: ArtifactIdValue
    artifact_type: ArtifactType
    payload_hash: Sha256Value
    parent_artifact_ids: tuple[ArtifactIdValue, ...]

    @field_validator("parent_artifact_ids")
    @classmethod
    def require_canonical_parents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value), key=lambda item: item.encode("utf-8"))):
            raise ValueError("artifact DAG parents must be sorted and unique")
        return value


def terminal_artifact_dag_hash(
    *,
    run_id: RunIdValue,
    terminal_snapshot_event_head_hash: Sha256Value,
    artifacts: tuple[TerminalArtifactDagEntry, ...],
) -> str:
    artifact_ids = tuple(entry.artifact_id for entry in artifacts)
    canonical_ids = tuple(
        sorted(set(artifact_ids), key=lambda item: item.encode("utf-8"))
    )
    if not artifacts or artifact_ids != canonical_ids:
        raise ValueError("terminal artifact DAG entries must be sorted and unique")
    known_ids = set(artifact_ids)
    parents_by_id = {
        entry.artifact_id: entry.parent_artifact_ids for entry in artifacts
    }
    if any(
        parent_id not in known_ids
        for entry in artifacts
        for parent_id in entry.parent_artifact_ids
    ):
        raise ValueError("terminal artifact DAG must contain every direct parent")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in visiting:
            raise ValueError("terminal artifact DAG must be acyclic")
        if artifact_id in visited:
            return
        visiting.add(artifact_id)
        for parent_id in parents_by_id[artifact_id]:
            visit(parent_id)
        visiting.remove(artifact_id)
        visited.add(artifact_id)

    for artifact_id in artifact_ids:
        visit(artifact_id)
    return (
        "sha256:"
        + sha256(
            canonical_json_bytes(
                {
                    "domain": "AutoMarkov-Terminal-Artifact-DAG-v1",
                    "run_id": run_id,
                    "terminal_snapshot_event_head_hash": (
                        terminal_snapshot_event_head_hash
                    ),
                    "artifacts": [
                        entry.model_dump(mode="json", round_trip=True, warnings="error")
                        for entry in artifacts
                    ],
                }
            )
        ).hexdigest()
    )


class TerminalArtifactDag(StrictFrozenModel):
    schema_version: Literal["automarkov.terminal-artifact-dag.v1"]
    run_id: RunIdValue
    terminal_snapshot_event_head_hash: Sha256Value
    artifacts: tuple[TerminalArtifactDagEntry, ...]
    closure_hash: Sha256Value

    @model_validator(mode="after")
    def require_canonical_closure(self) -> Self:
        expected = terminal_artifact_dag_hash(
            run_id=self.run_id,
            terminal_snapshot_event_head_hash=(self.terminal_snapshot_event_head_hash),
            artifacts=self.artifacts,
        )
        if self.closure_hash != expected:
            raise ValueError("terminal artifact DAG closure hash is inconsistent")
        return self


def _is_formal_or_environment_artifact(artifact_type: str) -> bool:
    return (
        artifact_type.startswith(("formal_", "environment_"))
        or artifact_type.endswith("_environment_binding")
        or artifact_type
        in {
            "decision_process_spec",
            "mdp_spec",
            "pomdp_spec",
            "markov_game_spec",
            "posg_spec",
        }
    )


def recompute_terminal_artifact_dag(
    repository: ArtifactRepository,
    terminal_result: TerminalResult,
) -> TerminalArtifactDag:
    if terminal_result.terminal_state != "CLARIFICATION_REQUIRED":
        raise ValueError("clarification DAG requires a CLARIFICATION_REQUIRED terminal")
    pending = [
        (reference.artifact_id, reference.payload_hash)
        for reference in terminal_result.payload_outputs
    ]
    if not pending:
        raise ValueError("clarification terminal requires a payload output root")
    discovered: dict[str, TerminalArtifactDagEntry] = {}
    while pending:
        artifact_id, expected_payload_hash = pending.pop()
        existing = discovered.get(artifact_id)
        if existing is not None:
            if existing.payload_hash != expected_payload_hash:
                raise ValueError("terminal artifact DAG has conflicting payload hashes")
            continue
        stored = repository.get(ArtifactId(root=artifact_id))
        if stored.envelope.payload_hash != expected_payload_hash:
            raise ValueError("terminal output payload hash does not match repository")
        artifact_type = stored.envelope.artifact_type
        if _is_formal_or_environment_artifact(artifact_type):
            raise ValueError(
                "clarification terminal DAG contains a formal or environment artifact"
            )
        parent_ids = tuple(
            parent.root for parent in stored.envelope.parent_artifact_ids
        )
        entry = TerminalArtifactDagEntry(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            payload_hash=stored.envelope.payload_hash,
            parent_artifact_ids=parent_ids,
        )
        discovered[artifact_id] = entry
        for parent_id in reversed(parent_ids):
            parent = repository.get(ArtifactId(root=parent_id))
            pending.append((parent_id, parent.envelope.payload_hash))
    artifacts = tuple(
        sorted(discovered.values(), key=lambda item: item.artifact_id.encode("utf-8"))
    )
    snapshot_hash = terminal_result.terminal_snapshot_event_head.event_hash.root
    return TerminalArtifactDag(
        schema_version="automarkov.terminal-artifact-dag.v1",
        run_id=terminal_result.run_id,
        terminal_snapshot_event_head_hash=snapshot_hash,
        artifacts=artifacts,
        closure_hash=terminal_artifact_dag_hash(
            run_id=terminal_result.run_id,
            terminal_snapshot_event_head_hash=snapshot_hash,
            artifacts=artifacts,
        ),
    )


def _require_canonical_signature(value: str) -> str:
    try:
        decoded = base64.urlsafe_b64decode(value + "==")
    except ValueError as error:
        raise ValueError("signature must be canonical Ed25519 base64url") from error
    if (
        len(decoded) != 64
        or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value
    ):
        raise ValueError("signature must be canonical Ed25519 base64url")
    return value


Ed25519Signature = Annotated[
    str,
    Field(strict=True, pattern=r"^[A-Za-z0-9_-]{86}$"),
    AfterValidator(_require_canonical_signature),
]


class _SignedClarificationArtifact(StrictFrozenModel):
    issued_at: CanonicalTimestamp
    nonce_b64url: CanonicalNonce
    signature_algorithm: Literal["Ed25519"]
    signature_b64url: Ed25519Signature


class _ExperimentClarificationSubject(StrictFrozenModel):
    experiment_id: NonEmptyId
    run_id: RunIdValue
    cell_id: NonEmptyId
    suite_id: NonEmptyId
    variant_id: Literal["v5_clarification_required"]
    track: Literal["AUTO"]
    method_id: NonEmptyId
    generation_pair_id: NonEmptyId


class ClarificationEvaluationRequest(
    _ExperimentClarificationSubject, _SignedClarificationArtifact
):
    schema_version: Literal["automarkov.clarification-evaluation-request.v1"]
    signing_domain: Literal["AutoMarkov-Clarification-Evaluation-Request-v1"]
    request_id: RequestIdValue
    pair_binding_id: NonEmptyId
    generation_seed: NonNegativeSafeInt
    run_manifest_artifact_id: ArtifactIdValue
    run_manifest_payload_hash: Sha256Value
    task_artifact_id: ArtifactIdValue
    task_payload_hash: Sha256Value
    review_report_artifact_id: ArtifactIdValue
    review_report_payload_hash: Sha256Value
    outcome_mask_artifact_id: ArtifactIdValue
    outcome_mask_payload_hash: Sha256Value
    clarification_result_artifact_id: ArtifactIdValue
    clarification_result_payload_hash: Sha256Value
    terminal_result_artifact_id: ArtifactIdValue
    terminal_result_payload_hash: Sha256Value
    terminal_event_id: EventId
    terminal_event_hash: Sha256Value
    terminal_snapshot_sequence_no: NonNegativeSafeInt
    terminal_snapshot_event_head_hash: Sha256Value
    execution_attestation_artifact_id: ArtifactIdValue
    execution_attestation_payload_hash: Sha256Value
    terminal_artifact_dag_closure_hash: Sha256Value
    clarification_oracle_commitment: Sha256Value
    evaluator_protocol_id: NonEmptyId
    evaluator_protocol_hash: Sha256Value
    evaluator_profile_id: NonEmptyId
    evaluator_profile_hash: Sha256Value
    evaluator_lock_hash: Sha256Value
    evaluator_image_hash: Sha256Value
    evaluator_schema_id: NonEmptyId
    evaluator_schema_hash: Sha256Value
    not_before: CanonicalTimestamp
    expires_at: CanonicalTimestamp
    coordinator_key_id: NonEmptyId

    @model_validator(mode="after")
    def require_terminal_and_deadline_binding(self) -> Self:
        not_before = datetime.fromisoformat(self.not_before)
        issued_at = datetime.fromisoformat(self.issued_at)
        expires_at = datetime.fromisoformat(self.expires_at)
        subject_ids = (
            self.run_manifest_artifact_id,
            self.task_artifact_id,
            self.review_report_artifact_id,
            self.outcome_mask_artifact_id,
            self.clarification_result_artifact_id,
            self.terminal_result_artifact_id,
            self.execution_attestation_artifact_id,
        )
        if not not_before <= issued_at <= expires_at:
            raise ValueError("clarification request deadline is inconsistent")
        if self.terminal_event_hash != self.terminal_snapshot_event_head_hash:
            raise ValueError("clarification request terminal snapshot is inconsistent")
        if len(subject_ids) != len(set(subject_ids)):
            raise ValueError("clarification request subjects must be distinct")
        return self


class ClarificationEvaluationVerdict(
    _ExperimentClarificationSubject, _SignedClarificationArtifact
):
    schema_version: Literal["automarkov.clarification-evaluation-verdict.v1"]
    signing_domain: Literal["AutoMarkov-Clarification-Evaluation-Verdict-v1"]
    verdict_id: NonEmptyId
    request_id: RequestIdValue
    request_payload_hash: Sha256Value
    outcome_mask_artifact_id: ArtifactIdValue
    outcome_mask_payload_hash: Sha256Value
    clarification_result_artifact_id: ArtifactIdValue
    clarification_result_payload_hash: Sha256Value
    terminal_result_artifact_id: ArtifactIdValue
    terminal_result_payload_hash: Sha256Value
    execution_attestation_artifact_id: ArtifactIdValue
    execution_attestation_payload_hash: Sha256Value
    terminal_artifact_dag_closure_hash: Sha256Value
    safe_clarification_required: bool
    evaluator_key_id: NonEmptyId


class ClarificationContinuationPolicy(_SignedClarificationArtifact):
    schema_version: Literal["automarkov.clarification-continuation-policy.v1"]
    signing_domain: Literal["AutoMarkov-Clarification-Continuation-Policy-v1"]
    authority_principal_id: PrincipalIdValue
    signing_key_id: NonEmptyId
    authority_status: Literal["active"]
    preregistration_artifact_id: ArtifactIdValue
    preregistration_payload_hash: Sha256Value
    child_ordinal_increment: Literal[1]
    maximum_child_count: Literal[1]
    experiment_eligibility: Literal["nonconfirmatory"]
    allowed_answer_artifact_kinds: FrozenSequence[Literal["signed_answer_bundle"]]
    budget_reset_rule: Literal["fresh_child_budget"]
    runtime_reset_rule: Literal["revalidate_runtime"]

    @field_validator("allowed_answer_artifact_kinds")
    @classmethod
    def require_answer_kind(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != ("signed_answer_bundle",):
            raise ValueError("continuation policy requires the signed answer bundle")
        return value


class SignedAnswerBundle(_SignedClarificationArtifact):
    schema_version: Literal["automarkov.signed-answer-bundle.v1"]
    signing_domain: Literal["AutoMarkov-Signed-Answer-Bundle-v1"]
    principal_id: PrincipalIdValue
    signing_key_id: NonEmptyId
    answer_hash: Sha256Value
    preregistration_artifact_id: ArtifactIdValue
    preregistration_payload_hash: Sha256Value


ClarificationInvalidReason: TypeAlias = Literal[
    "generation_contract_failed",
    "missing_required_artifact",
    "evaluation_timeout",
    "evaluation_integrity_failure",
    "contamination",
    "protocol_violation",
]


class _ClarificationOutcomeBase(
    _ExperimentClarificationSubject, _SignedClarificationArtifact
):
    schema_version: Literal["automarkov.clarification-outcome.v1"]
    signing_domain: Literal["AutoMarkov-Clarification-Outcome-v1"]
    outcome_id: NonEmptyId
    pair_binding_id: NonEmptyId
    run_manifest_artifact_id: ArtifactIdValue
    run_manifest_payload_hash: Sha256Value
    outcome_mask_artifact_id: ArtifactIdValue
    outcome_mask_payload_hash: Sha256Value
    safe_clarification_required: bool
    analysis_key_id: NonEmptyId


class EvaluatedClarificationOutcome(_ClarificationOutcomeBase):
    outcome_kind: Literal["evaluated"]
    reason: None
    terminal_result_artifact_id: ArtifactIdValue
    terminal_result_payload_hash: Sha256Value
    request_id: RequestIdValue
    request_payload_hash: Sha256Value
    verdict_id: NonEmptyId
    verdict_payload_hash: Sha256Value


class InvalidClarificationOutcome(_ClarificationOutcomeBase):
    outcome_kind: Literal["invalid"]
    terminal_result_artifact_id: ArtifactIdValue | None
    terminal_result_payload_hash: Sha256Value | None
    request_id: RequestIdValue | None
    request_payload_hash: Sha256Value | None
    verdict_id: NonEmptyId | None
    verdict_payload_hash: Sha256Value | None
    reason: ClarificationInvalidReason

    @model_validator(mode="after")
    def require_zero_and_reason_cardinality(self) -> Self:
        for identity, payload_hash in (
            (self.terminal_result_artifact_id, self.terminal_result_payload_hash),
            (self.request_id, self.request_payload_hash),
            (self.verdict_id, self.verdict_payload_hash),
        ):
            if (identity is None) != (payload_hash is None):
                raise ValueError(
                    "clarification outcome references require ID/hash pairs"
                )
        if self.verdict_id is not None and self.request_id is None:
            raise ValueError("a clarification verdict requires its request")
        if self.request_id is not None and self.terminal_result_artifact_id is None:
            raise ValueError("a clarification request requires its terminal result")
        if self.safe_clarification_required:
            raise ValueError("invalid clarification outcomes map to zero")
        if self.reason in {
            "generation_contract_failed",
            "missing_required_artifact",
        } and (self.request_id is not None or self.verdict_id is not None):
            raise ValueError(
                "pre-evaluation failure cannot fabricate request or verdict"
            )
        if self.reason == "evaluation_timeout" and (
            self.request_id is None or self.verdict_id is not None
        ):
            raise ValueError("evaluation timeout requires request without verdict")
        return self


ClarificationOutcomeRecord: TypeAlias = Annotated[
    EvaluatedClarificationOutcome | InvalidClarificationOutcome,
    Field(discriminator="outcome_kind"),
]


SignedClarificationArtifact: TypeAlias = (
    ClarificationEvaluationRequest
    | ClarificationEvaluationVerdict
    | ClarificationContinuationPolicy
    | SignedAnswerBundle
    | EvaluatedClarificationOutcome
    | InvalidClarificationOutcome
)


def clarification_signature_preimage(value: SignedClarificationArtifact) -> bytes:
    payload = value.model_dump(mode="json", round_trip=True, warnings="error")
    del payload["signature_b64url"]
    return canonical_json_bytes(payload)


def verify_clarification_signature(
    value: SignedClarificationArtifact,
    public_key: Ed25519PublicKey,
) -> None:
    try:
        public_key.verify(
            base64.urlsafe_b64decode(value.signature_b64url + "=="),
            clarification_signature_preimage(value),
        )
    except (InvalidSignature, ValueError) as error:
        raise ValueError("clarification artifact signature is invalid") from error


def _clarification_payload_hash(value: StrictFrozenModel) -> str:
    payload = value.model_dump(mode="json", round_trip=True, warnings="error")
    return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()


def validate_clarification_evaluation_binding(
    request: ClarificationEvaluationRequest,
    verdict: ClarificationEvaluationVerdict,
) -> None:
    pairs = (
        (verdict.request_id, request.request_id),
        (verdict.request_payload_hash, _clarification_payload_hash(request)),
        (verdict.experiment_id, request.experiment_id),
        (verdict.run_id, request.run_id),
        (verdict.cell_id, request.cell_id),
        (verdict.suite_id, request.suite_id),
        (verdict.variant_id, request.variant_id),
        (verdict.track, request.track),
        (verdict.method_id, request.method_id),
        (verdict.generation_pair_id, request.generation_pair_id),
        (verdict.outcome_mask_artifact_id, request.outcome_mask_artifact_id),
        (verdict.outcome_mask_payload_hash, request.outcome_mask_payload_hash),
        (
            verdict.clarification_result_artifact_id,
            request.clarification_result_artifact_id,
        ),
        (
            verdict.clarification_result_payload_hash,
            request.clarification_result_payload_hash,
        ),
        (verdict.terminal_result_artifact_id, request.terminal_result_artifact_id),
        (verdict.terminal_result_payload_hash, request.terminal_result_payload_hash),
        (
            verdict.execution_attestation_artifact_id,
            request.execution_attestation_artifact_id,
        ),
        (
            verdict.execution_attestation_payload_hash,
            request.execution_attestation_payload_hash,
        ),
        (
            verdict.terminal_artifact_dag_closure_hash,
            request.terminal_artifact_dag_closure_hash,
        ),
    )
    if any(actual != expected for actual, expected in pairs) or not (
        datetime.fromisoformat(request.issued_at)
        <= datetime.fromisoformat(verdict.issued_at)
        <= datetime.fromisoformat(request.expires_at)
    ):
        raise ValueError("clarification verdict subjects do not match request")


def validate_clarification_outcome_binding(
    outcome: EvaluatedClarificationOutcome,
    request: ClarificationEvaluationRequest,
    verdict: ClarificationEvaluationVerdict,
) -> None:
    pairs = (
        (outcome.experiment_id, request.experiment_id),
        (outcome.run_id, request.run_id),
        (outcome.cell_id, request.cell_id),
        (outcome.suite_id, request.suite_id),
        (outcome.variant_id, request.variant_id),
        (outcome.track, request.track),
        (outcome.method_id, request.method_id),
        (outcome.generation_pair_id, request.generation_pair_id),
        (outcome.pair_binding_id, request.pair_binding_id),
        (outcome.run_manifest_artifact_id, request.run_manifest_artifact_id),
        (outcome.run_manifest_payload_hash, request.run_manifest_payload_hash),
        (outcome.outcome_mask_artifact_id, request.outcome_mask_artifact_id),
        (outcome.outcome_mask_payload_hash, request.outcome_mask_payload_hash),
        (outcome.terminal_result_artifact_id, request.terminal_result_artifact_id),
        (outcome.terminal_result_payload_hash, request.terminal_result_payload_hash),
        (outcome.request_id, request.request_id),
        (outcome.request_payload_hash, _clarification_payload_hash(request)),
        (outcome.verdict_id, verdict.verdict_id),
        (outcome.verdict_payload_hash, _clarification_payload_hash(verdict)),
        (
            outcome.safe_clarification_required,
            verdict.safe_clarification_required,
        ),
    )
    if any(actual != expected for actual, expected in pairs):
        raise ValueError("clarification outcome subjects do not match verdict")


class ClarificationReplayConflictError(RuntimeError):
    pass


def _clarification_signing_key_id(value: SignedClarificationArtifact) -> str:
    if isinstance(value, ClarificationEvaluationRequest):
        return value.coordinator_key_id
    if isinstance(value, ClarificationEvaluationVerdict):
        return value.evaluator_key_id
    if isinstance(value, EvaluatedClarificationOutcome | InvalidClarificationOutcome):
        return value.analysis_key_id
    return value.signing_key_id


def _replay_key(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _clarification_replay_claims(
    value: SignedClarificationArtifact,
) -> tuple[tuple[str, str], ...]:
    signing_key_id = _clarification_signing_key_id(value)
    claims: list[tuple[str, str]] = [
        (
            "signing_nonce",
            _replay_key(
                {
                    "signing_domain": value.signing_domain,
                    "signing_key_id": signing_key_id,
                    "nonce_b64url": value.nonce_b64url,
                }
            ),
        )
    ]
    if isinstance(value, ClarificationEvaluationRequest):
        payload = value.model_dump(mode="json", round_trip=True, warnings="error")
        for key in (
            "request_id",
            "issued_at",
            "not_before",
            "expires_at",
            "nonce_b64url",
            "coordinator_key_id",
            "signature_algorithm",
            "signature_b64url",
        ):
            del payload[key]
        claims.extend(
            (
                ("request_id", value.request_id),
                (
                    "coordinator_run",
                    _replay_key(
                        {
                            "coordinator_key_id": value.coordinator_key_id,
                            "run_id": value.run_id,
                        }
                    ),
                ),
                ("request_subject", _replay_key(payload)),
            )
        )
    elif isinstance(value, ClarificationEvaluationVerdict):
        claims.extend(
            (
                ("verdict_id", value.verdict_id),
                ("verdict_request", value.request_id),
            )
        )
    elif isinstance(value, EvaluatedClarificationOutcome | InvalidClarificationOutcome):
        claims.extend(
            (
                ("outcome_id", value.outcome_id),
                (
                    "outcome_slot",
                    _replay_key(
                        {
                            "experiment_id": value.experiment_id,
                            "run_id": value.run_id,
                            "outcome_mask_artifact_id": (
                                value.outcome_mask_artifact_id
                            ),
                        }
                    ),
                ),
            )
        )
    return tuple(sorted(claims, key=lambda item: item[0].encode("utf-8")))


class InMemoryClarificationReplayIndex:
    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], bytes] = {}
        self._lock = RLock()

    def record(self, value: SignedClarificationArtifact) -> bool:
        canonical_bytes = canonical_json_bytes(
            value.model_dump(mode="json", round_trip=True, warnings="error")
        )
        claims = _clarification_replay_claims(value)
        with self._lock:
            existing = tuple(self._claims.get(claim) for claim in claims)
            if (
                any(
                    stored is not None and stored != canonical_bytes
                    for stored in existing
                )
                or any(stored is None for stored in existing)
                and any(stored is not None for stored in existing)
            ):
                raise ClarificationReplayConflictError(
                    "clarification replay claim conflicts with persisted bytes"
                )
            if all(stored is not None for stored in existing):
                return False
            self._claims.update(dict.fromkeys(claims, canonical_bytes))
        return True

    def close(self) -> None:
        pass


class SqliteClarificationReplayIndex:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS clarification_replay_claims ("
            "claim_kind TEXT NOT NULL, claim_key TEXT NOT NULL, "
            "canonical_bytes BLOB NOT NULL, PRIMARY KEY (claim_kind, claim_key)) "
            "STRICT"
        )
        self._lock = RLock()

    def record(self, value: SignedClarificationArtifact) -> bool:
        canonical_bytes = canonical_json_bytes(
            value.model_dump(mode="json", round_trip=True, warnings="error")
        )
        claims = _clarification_replay_claims(value)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = tuple(
                    self._connection.execute(
                        "SELECT canonical_bytes FROM clarification_replay_claims "
                        "WHERE claim_kind = ? AND claim_key = ?",
                        claim,
                    ).fetchone()
                    for claim in claims
                )
                stored_bytes = tuple(
                    None if row is None else cast(bytes, row[0]) for row in existing
                )
                if (
                    any(
                        stored is not None and stored != canonical_bytes
                        for stored in stored_bytes
                    )
                    or any(stored is None for stored in stored_bytes)
                    and any(stored is not None for stored in stored_bytes)
                ):
                    raise ClarificationReplayConflictError(
                        "clarification replay claim conflicts with persisted bytes"
                    )
                if all(stored is not None for stored in stored_bytes):
                    self._connection.commit()
                    return False
                self._connection.executemany(
                    "INSERT INTO clarification_replay_claims("
                    "claim_kind, claim_key, canonical_bytes) VALUES (?, ?, ?)",
                    [(*claim, canonical_bytes) for claim in claims],
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return True

    def close(self) -> None:
        self._connection.close()
