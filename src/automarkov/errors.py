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
