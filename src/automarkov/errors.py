from __future__ import annotations


class AutoMarkovError(RuntimeError):
    code = "automarkov_error"


class UnknownRunError(AutoMarkovError):
    code = "unknown_run"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"unknown run: {run_id}")


class RunIdCollisionError(AutoMarkovError):
    code = "run_id_collision"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run ID already exists: {run_id}")


class CapabilityDeferredError(AutoMarkovError):
    code = "capability_deferred"

    def __init__(self, capability: str, owner_ticket: str) -> None:
        self.capability = capability
        self.owner_ticket = owner_ticket
        super().__init__(f"{capability} is deferred to {owner_ticket}")


class ArtifactSchemaError(AutoMarkovError):
    code = "artifact_schema_error"

    def __init__(self, artifact_type: str, schema_version: str | None) -> None:
        self.artifact_type = artifact_type
        self.schema_version = schema_version
        super().__init__(
            "unknown or mismatched artifact schema: "
            f"{artifact_type}@{schema_version or '<missing>'}"
        )


class ArtifactSchemaConflictError(AutoMarkovError):
    code = "artifact_schema_conflict"

    def __init__(self, artifact_type: str, schema_version: str) -> None:
        self.artifact_type = artifact_type
        self.schema_version = schema_version
        super().__init__(
            "persistent artifact schema contract conflicts with this runtime: "
            f"{artifact_type}@{schema_version}"
        )


class CanonicalPayloadError(AutoMarkovError):
    code = "canonical_payload_rejected"

    def __init__(self, artifact_type: str, schema_version: str | None) -> None:
        self.artifact_type = artifact_type
        self.schema_version = schema_version
        super().__init__(
            "artifact payload failed canonical schema validation: "
            f"{artifact_type}@{schema_version or '<missing>'}"
        )


class MissingArtifactParentError(AutoMarkovError):
    code = "missing_artifact_parent"

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(f"missing artifact parent: {artifact_id}")


class ArtifactParentContractError(AutoMarkovError):
    code = "artifact_parent_contract_error"

    def __init__(
        self,
        artifact_type: str,
        expected_types: tuple[str, ...],
        actual_types: tuple[str, ...],
    ) -> None:
        self.artifact_type = artifact_type
        self.expected_types = expected_types
        self.actual_types = actual_types
        super().__init__(
            "artifact direct-parent types do not match the registered contract: "
            f"{artifact_type} expected={expected_types!r} actual={actual_types!r}"
        )


class UnknownArtifactError(AutoMarkovError):
    code = "unknown_artifact"

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(f"unknown artifact: {artifact_id}")


class ArtifactCycleError(AutoMarkovError):
    code = "artifact_cycle"

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(f"artifact parent graph would contain a cycle: {artifact_id}")


class ArtifactIdentityConflictError(AutoMarkovError):
    code = "artifact_identity_conflict"

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(
            f"artifact identity has different canonical bytes: {artifact_id}"
        )


class ArtifactIntegrityError(AutoMarkovError):
    code = "artifact_integrity_error"

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        super().__init__(
            f"stored artifact failed integrity verification: {artifact_id}"
        )


class ArtifactWriteAuthorityError(AutoMarkovError):
    code = "artifact_write_authority_error"

    def __init__(self, artifact_type: str) -> None:
        self.artifact_type = artifact_type
        super().__init__(
            f"artifact type requires its authenticated lifecycle command: {artifact_type}"
        )


class EventSchemaError(AutoMarkovError):
    code = "event_schema_error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"run event failed strict schema validation: {detail}")


class UnknownEventError(AutoMarkovError):
    code = "unknown_event"

    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
        super().__init__(f"unknown run event type: {event_type}")


class EventIntegrityError(AutoMarkovError):
    code = "event_integrity_error"

    def __init__(self, subject: str) -> None:
        self.subject = subject
        super().__init__(f"stored run event failed integrity verification: {subject}")


class EventSequenceConflictError(AutoMarkovError):
    code = "event_sequence_conflict"

    def __init__(self, run_id: str, sequence_no: int) -> None:
        self.run_id = run_id
        self.sequence_no = sequence_no
        super().__init__(f"run event sequence conflicts at {run_id}:{sequence_no}")


class EventHeadConflictError(AutoMarkovError):
    code = "event_head_conflict"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run event head compare-and-swap failed: {run_id}")


class EventReplayConflictError(AutoMarkovError):
    code = "event_replay_conflict"

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(f"run event identity or nonce was replayed: {event_id}")


class CommandAuthenticationError(AutoMarkovError):
    code = "command_authentication_error"

    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id
        super().__init__(
            f"lifecycle command principal is not authenticated: {principal_id}"
        )


class InvalidRunTransitionError(AutoMarkovError):
    code = "invalid_run_transition"

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"invalid run state transition: {from_state} -> {to_state}")


class RunTerminalError(AutoMarkovError):
    code = "run_terminal"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run is terminal and cannot accept this event: {run_id}")


class RunResumeContractError(AutoMarkovError):
    code = "run_resume_contract_error"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(
            f"run recovery does not match the frozen wait contract: {run_id}"
        )


class BudgetContractError(AutoMarkovError):
    code = "budget_contract_error"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run budget snapshot violates its frozen contract: {run_id}")


class TerminalCommitRequiredError(AutoMarkovError):
    code = "terminal_commit_required"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(
            f"terminal transition requires an atomic terminal commit: {run_id}"
        )


class TerminalProvenanceError(AutoMarkovError):
    code = "terminal_provenance_error"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"terminal provenance does not match the run: {run_id}")


class RunProjectionHeadError(AutoMarkovError):
    code = "run_projection_head_error"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"requested run projection head is unavailable: {run_id}")


class RunProjectorIdentityError(AutoMarkovError):
    code = "run_projector_identity_error"

    def __init__(self, projector_version: str) -> None:
        self.projector_version = projector_version
        super().__init__(
            f"requested run projector identity is unavailable: {projector_version}"
        )


class LocalLlmRuntimeStateError(AutoMarkovError):
    code = "local_llm_runtime_state_error"

    def __init__(self, state: str) -> None:
        self.state = state
        super().__init__(f"local LLM runtime is not ready: {state}")


class LocalLlmRuntimeCapacityError(AutoMarkovError):
    code = "local_llm_runtime_capacity_error"

    def __init__(self, runtime_id: str) -> None:
        self.runtime_id = runtime_id
        super().__init__(f"local LLM runtime capacity is exhausted: {runtime_id}")


class EvidenceCapabilityDeniedError(AutoMarkovError):
    code = "evidence_capability_denied"

    def __init__(self, principal_id: str, store_id: str) -> None:
        self.principal_id = principal_id
        self.store_id = store_id
        super().__init__(
            "evidence capability does not authorize the requested store: "
            f"{principal_id}/{store_id}"
        )
