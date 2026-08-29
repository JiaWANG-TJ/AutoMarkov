# Schema migration and write-version policy

## Status

PROPOSED

## Context

AutoMarkov uses Pydantic `StrictFrozenModel` (`strict=True`, `frozen=True`, `extra="forbid"`) as the base of all domain types. Every artifact payload declares a `schema_version: Literal["automarkov.<name>.vN"]` field whose value must exactly match the registered version in `ArtifactSchemaRegistry`. Artifact storage uses content-addressed immutable records in SQLite (`_SQLITE_SCHEMA_VERSION = 10` for the storage DDL layer, independent of payload versions).

Three problems remain unsolved:

1. **No explicit write-target tracking.** The `ArtifactSchemaRegistry` in `repository.py` accepts per-call `(artifact_type, schema_version)` during registration but does not maintain which version is the active write target. Each call site independently decides the target version, creating a risk of version drift across concurrent pipelines.

2. **No documented migration path.** When a payload model gains a field, tightens a type constraint, or changes validation logic, there is no agreed procedure for producing a new schema version while keeping the old one readable.

3. **Free-text semantic gaps.** `decision_process.py` stores `transition_kernel: str`, `initial_distribution: str`, `observation_kernel: str` and other kernel references as plain strings. The concrete Gymnasium CartPole fixture uses human-readable descriptions (e.g. `"Gymnasium CartPoleEnv v1.2.2 equations with tau=0.02 seconds and kinematics_integrator='euler'"`). These gaps are intentional during investigation but will eventually need formalization as structured schemas, which requires a documented strategy for `str` -> structured type transitions.

## Decision

### D1. Schema registry gains explicit write-version tracking

Extend `ArtifactSchemaRegistry` with a `_write_versions: dict[str, str]` mapping that records the active schema version per artifact type. A new `set_write_version(artifact_type, schema_version)` method declares the write target. The method validates that `(artifact_type, schema_version)` is already registered and that each `artifact_type` has at most one active write version. Multiple concurrent pipelines that need different write targets must use distinct artifact types or converge to a single write version before the registry freezes.

```
registry = ArtifactSchemaRegistry()

# Register historical and current versions
registry.register("task_contract", "automarkov.task-contract.v1", TaskContractV1, ...)
registry.register("task_contract", "automarkov.task-contract.v2", TaskContractV2, ...)

# Declare the active write target
registry.set_write_version("task_contract", "automarkov.task-contract.v2")

# Freeze — no further registration or write-version changes allowed
registry.freeze()
```

Three query methods are added:

- `get_write_schema(artifact_type)` returns the `_RegisteredSchema` for the active write version. All code that produces new artifacts must call this method rather than hard-coding a version string.
- `get_read_schema(artifact_type, schema_version)` returns the `_RegisteredSchema` for any registered version, including historical ones. Used when loading or verifying existing artifacts.
- `is_write_version(artifact_type, schema_version)` returns `True` iff the given version is the active write target.

The `_RegisteredSchema` dataclass remains unchanged. The registry's existing `freeze()` method gains a validation step: every registered `artifact_type` must have exactly one `set_write_version()` call before freezing.

### D2. Single active write version, immutable historical versions

Each artifact type has exactly one active write-version at freeze time. Old versions become read-only: they continue to validate incoming historical artifacts but are never selected by `get_write_version()`. Payloads carry the version in their own `schema_version` Literal field — the Pydantic type system enforces exact matching at validation time, and the registry enforces the write policy at dispatch time.

This is compatible with the existing pattern. Models like `FixedCommitJobRequest` already declare multi-version Literals:

```python
class FixedCommitJobRequest(StrictFrozenModel):
    schema_version: Literal[
        "automarkov.fixed-commit-job-request.v1",
        "automarkov.fixed-commit-job-request.v2",
    ]
```

Under this ADR, such a model supports reading both v1 and v2 payloads. The write-target method selects exactly one version for new artifacts.

### D3. Migration creates new immutable artifacts, never in-place rewrites

A schema migration produces a brand-new content-addressed artifact with a fresh `ArtifactId` and distinct `payload_hash`. The original artifact remains readable at its original identity indefinitely. This is a direct consequence of ADR-0001 (immutable artifacts and append-only events).

Migration tool interface:

```python
def migrate_artifact_payload(
    old_payload_bytes: bytes,
    old_schema_version: str,
    new_schema_version: str,
    registry: ArtifactSchemaRegistry,
) -> bytes:
    """Deterministic, idempotent payload upgrade.

    Input:  raw bytes of an artifact whose schema_version == old_schema_version.
    Output: raw bytes valid against the new-schema model.

    The function must:
    - parse old_payload_bytes against the old-schema model
    - construct a new-schema payload with every field explicitly set
    - return canonical JSON bytes of the new payload
    - be pure (no I/O, no randomness, no side effects)
    """
```

Each `(old_version, new_version)` pair requires one registered migration function. The registry stores these alongside the schema pair. Migration is strictly one-directional; reverse migrations produce a new artifact from scratch, not by calling a backward function.

### D4. Version-increment triggers

A `schema_version` bump is triggered when any of the following occurs:

| Change kind | Example | Required action |
|---|---|---|
| Field added (non-optional) | `constraint_violation_limit: PositiveInt` added to `ConstraintSpec` | New version + migration that supplies the default |
| Field removed | `correlation_group` removed from `StochasticRewardSpec` | New version + migration that drops the field |
| Field type tightened | `kind: str` -> `kind: Literal["hard", "soft"]` | New version + migration that maps old values |
| Model type restructure | Union `A \| B` gains a new branch `C` | New version if discriminator changes |
| Validation logic change | `model_validator` adds a new rejection | New version if old payloads become invalid |

Changes that do NOT trigger a version bump:

| Change kind | Reason |
|---|---|
| Validator softens (accepts strictly more values) | Old payloads remain valid |
| Docstring or comment change | Payload shape unchanged |
| Internal method refactor (no schema effect) | Model dump unchanged |

### D5. Free-text field formalization strategy

`decision_process.py` declares several `str`-typed fields that carry semantic content:

- `transition_kernel`, `initial_distribution` (in `DecisionProcessBase`)
- `observation_kernel` (in `POMDPSpec`)
- `joint_action_kernel`, `solution_concept` (in `MGSpec`, `POSGSpec`)
- `expression` (in `DeterministicRewardSpec`)
- `distribution_family`, `expectation_expression` (in `StochasticRewardSpec`)
- `active_actor_function`, `cycle_boundary` (in `AECTurnSpec`)
- `symbol_id`, `binding_expression` (in `SymbolicNumericBounds`, `SymbolicDimension`)
- `predicate`, `violation_response` (in `ConstraintSpec`)
- `outcome_expression` (in `RiskSpec`)

These fields remain as `str` during the current investigation phase. When sufficient domain evidence exists (object catalogs, evaluation coverage, test fixtures), formalization proceeds through the standard migration path (D3): a new `v2` model replaces the `str` field with a typed structured field, the registry increments the write version, and a deterministic migration function converts old string payloads to new structured payloads.

The old `str` values remain readable as historical artifacts at `v1`. There is no auto-coercion, no `__get_validators__` override, and no silent type promotion.

| Phase | Field type | Schema artifact version | Notes |
|---|---|---|---|
| Investigation | `str` | `v1` | Free-text, evidence gathering |
| Formalization | `StructuredSpec` (new Pydantic model) | `v2` | Old `str` values migrated to structured |

### D6. Write-version policy for concurrent pipelines

When multiple pipelines operate concurrently (e.g. authoring pipeline at `v2`, RL training pipeline at `v1`), the registry design allows both patterns:

- **Converged write target (preferred):** Both pipelines call `set_write_version()` with the same version before the registry freezes. All new artifacts use the same schema.
- **Distinct artifact types:** If two pipelines genuinely require different payload shapes for the same conceptual entity, they must use distinct `artifact_type` names (e.g. `decision_process_spec` and `decision_process_training_spec`), each with its own registered schema and write version.

The `set_write_version()` method rejects a second call that changes an already-set version for the same artifact type. If a pipeline discovers it needs a different version after the write target is set, it must coordinate with other pipelines to agree on the new target, or use a distinct artifact type.

### D7. Event schemas are out of scope for write-version policy

Event schemas (tracked in `event_schema_contracts` SQLite table) are append-only and never rewritten. ADR-0001 establishes that events are immutable once persisted. Therefore event schema versions are developer-controlled monotonic increments, not subject to write-version selection. The existing pattern in `lifecycle.py` where each event class declares its own `schema_version: Literal[...]` remains unchanged.

The migration policy (D3) and version-increment triggers (D4) apply identically to event schemas.

### D8. SQLite DDL version is independent

`_SQLITE_SCHEMA_VERSION` (currently `10`) and `_SQLITE_SCHEMA_STATEMENTS` control the storage-layer table layout. This version is independent from payload schema versions. DDL migrations are handled by the existing `_sqlite_schema_rows()` comparison at startup time and the `_SQLITE_V8_TO_V10_TABLES` transition set. This ADR does not change the DDL migration mechanism.

## Consequences

- Every code path that produces a new artifact must call `registry.get_write_schema(artifact_type)` instead of hard-coding a `schema_version` string. Existing call sites that use a fixed string must be audited and migrated.
- The `_write_versions` mapping adds a new freeze-time invariant: if any registered artifact type lacks a `set_write_version()` call, the registry refuses to freeze. This is a breaking change for existing freeze call sites.
- Models that declare multi-version Literals (like `FixedCommitJobRequest`) continue to work for reading historical payloads. The write-target selection narrows which of those versions receives new artifacts.
- Free-text `str` fields in `decision_process.py` remain intentionally free-text during the evaluation phase. A future schema version bump will introduce structured replacements; old `str` payloads remain valid at their historical version.
- Migration tooling must be deterministic and idempotent. Each `(old_version, new_version)` pair requires a registered migration function with explicit field-change records. Migration produces new immutable artifacts; the original is never modified.
- The registry exposes no method to unregister a schema or change a write version after freeze. This prevents post-hoc version manipulation.
- The `schema_version` Literal field in each Pydantic model continues to serve as the validation-time version check. The registry's `get_write_schema()` serves as the dispatch-time write-target selection. Both mechanisms must agree.