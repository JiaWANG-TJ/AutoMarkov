# Contributing to AutoMarkov

Thank you for your interest in contributing to AutoMarkov. This document
covers the process, standards, and expectations for all contributions.

## Code of Conduct

All participants are expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Please read it before
your first contribution.

## Getting Started

### Prerequisites

- Python 3.11+
- `uv` package manager (see [uv docs](https://docs.astral.sh/uv/))

### Environment Setup

```bash
# Clone the repository
git clone https://github.com/<org>/AutoMarkov.git
cd AutoMarkov

# Create and activate the locked environment
uv sync --locked

# Verify provenance metadata (no profile dependencies installed)
uv run automarkov verify-provenance --repository-root .

# Run the core contract tests
uv run pytest -q tests/unit/test_canonical_json.py \
  tests/contract/test_artifact_repository.py \
  tests/contract/test_artifact_schema_registry.py
```

## Development Workflow

### Branch Strategy

- `main` is the integration branch. All pull requests target `main`.
- Create feature branches from the latest `main`:
  - `feat/<short-description>` for new functionality
  - `fix/<short-description>` for bug fixes
  - `docs/<short-description>` for documentation-only changes
- Keep branches rebased on `main`; avoid merge commits inside
  feature branches.

### Commit Messages

Use imperative mood, 72-character subject line, and a body that
explains *why* the change is needed. Prefix the subject with a
conventional tag:

```
feat: add multi-agent suite adapter for PettingZoo 1.4
```

Tags: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`,
`perf`, `ci`.

### Pull Request Guidelines

1. Every PR must pass the full `uv run pytest` suite before
   requesting review.
2. Include or update tests that cover the changed behaviour.
3. Keep PRs focused: one logical change per PR.
4. Reference the tracking issue when one exists.
5. PRs that touch security-sensitive modules (`environment_sandbox`,
   `sealed_evaluation`, `validation_contracts`, `evidence_access`,
   `evidence_contracts`) require explicit maintainer review.

## Testing

All tests live under `tests/`. Run the full suite with:

```bash
uv run pytest -q
```

Contract and schema tests are the acceptance gate. If they fail the PR
cannot merge. Performance and integration tests are advisory on PRs and
blocking on release tags.

## Schema and ADR Rules

### Specifications

AutoMarkov uses formal contract types defined in `*_contracts.py` files.
When you add or modify a contract:

1. Update the corresponding Pydantic model.
2. Add or update a canonical JSON round-trip test.
3. Register or update the schema entry in the artifact schema
   registry when applicable.

### Architectural Decisions

Significant design choices should be recorded as ADRs under the
`docs/adr/` directory using the template:

```
# ADR-NNN: <title>

## Status
Proposed | Accepted | Deprecated | Superseded

## Context
What is the issue that motivates this decision?

## Decision
What is the change being proposed or decided?

## Consequences
What becomes easier or harder because of this?
```

ADR filenames follow the pattern `NNN-<slug>.md`.

## Repository Safety

- Raw web captures, checkpoints, sealed evaluators, external research
  checkouts, and complete experiment outputs remain in ignored artifact
  roots; only redacted manifests and compact reports are publishable.
- Real secrets go in `.env` (mode `0600`, never committed).
- Restricted research repositories and sealed evaluation assets are never
  vendored.