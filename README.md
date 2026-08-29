# AutoMarkov

> **NOT EXPERIMENT READY** -- No end-to-end RL experiments have been executed.
> **NOT RELEASE READY** -- Static analysis passes only; no verified runtime.

AutoMarkov is an evidence-grounded compiler for sequential decision problems.
It turns an approved natural-language task contract into a typed MDP, POMDP,
Markov game, or POSG specification. The compiler core is installable and
produces typed JSON views. Runtime integrations (Gymnasium/PettingZoo bindings,
RLlib training, production adapters, sealed evaluation) are under active
development and **not yet verified for use**.

The project is designed around six deep/public seams: `Compiler`,
`ArtifactRepository`, `LocalLlmRuntime`, `EvidenceGateway`, `ExecutionSandbox`,
and one environment-execution seam exposed through the narrow
`EnvironmentBinding`/`TrainingRunner` views. Generated artifacts are immutable and
content addressed; approvals and run transitions are append-only events.

## Status

AutoMarkov is under active development. Current verified capabilities:

- **Compiler core**: installable package, strict intake types, eight public
  protocol views, bounded canonical JSON codecs.
- **Artifact stores**: immutable content-addressed repositories (memory and
  transactional SQLite).
- **Lifecycle**: append-only reducer, atomic terminal transitions,
  cross-run transactions, specified-head replay.
- **Provenance catalog**: 17 isolated runtime profiles.
- **CLI**: `compile` (typed JSON view), `verify-provenance`
  (metadata verification), `pilot run` (engineering pilot).

**Not yet verified** (TDD only, no production smoke):

- RLlib training runner and statistics aggregation.
- Gymnasium/PettingZoo environment adapter pass-through in production mode.
- `ScriptedTrainingRunner` and all suite adapters
  (Taxi, MiniGrid, MetaDrive, MPE2, SMACv2, CityLearn)
 -- these are code-complete but **not integration-tested end-to-end**.
- Sealed evaluation gate (T16) and FixCommitRunner (T17).
- Release pipeline (`RedactedArtifactBinding`, `RedactionManifest`).

## Development

Create the locked environment and run static analysis:

```bash
uv sync --locked
uv run ruff check src/
uv run pyright src/
```

Run the full unit and contract test suite:

```bash
uv run pytest -q tests/
```

Verify the pinned upstream catalog, profile locks, SBOMs, license manifests,
Linux/amd64 selected artifacts, and isolation rules without installing the
profile-specific dependencies:

```bash
uv run automarkov verify-provenance --repository-root .
```

This metadata verification does not claim that a profile image or attached
runtime is ready. Profile-local import smokes, OCI image build, and attached
service canaries are separate gates; only a verified build may acquire an OCI
image digest.

The CLI provides three commands:

```bash
# Compile a task contract into a typed JSON view
uv run automarkov compile \
  --request-id request_demo \
  --task-text "Model a finite-horizon inventory decision process."

# Verify pinned upstream metadata
uv run automarkov verify-provenance --repository-root .

# Run an engineering pilot (requires --manifest)
uv run automarkov pilot run --manifest <path>
```

### Static analysis

| Tool    | Count | Status |
|---------|-------|--------|
| Ruff    | 0 errors | All checks pass |
| Pyright  | 30 errors | `benchmark_suites.py`, `generation_methods.py`, `statistics.py` have type errors |

### Profiles

17 profiles defined. 16 are `recipe_frozen`; 1 (`llm-qwen36-vllm`) is
`attached_unverified`. No profile has passed a production readiness gate.

### CI

The `provenance-contract` workflow runs on push/PR with two jobs:
`metadata` and `import-smoke`. The metadata job includes provenance
verification and a focused pytest subset. The import-smoke job is
`workflow_dispatch` only.

## Runtime policy

- Generative inference uses a local Qwen3.6-35B-A3B vLLM service only.
- Tavily is restricted to Search, Extract, and Crawl with hosted answers disabled.
- Reinforcement learning uses PyTorch and RLlib through isolated dependency profiles.
- SwanLab is offline-first.
- Restricted research repositories and sealed evaluation assets are never vendored.

## Known blockers

- Pyright: 30 type errors in `benchmark_suites.py`, `generation_methods.py`,
  `statistics.py` (type alias variables used in type expressions).
- No production RLlib runner integration-tested.
- Issue T18--T27 closed but acceptance evidence incomplete.
- All profiles remain `recipe_frozen` or `attached_unverified`.

## Repository safety

Create `.env` from `.env.example`. The real file is ignored by Git and must have
mode `0600`. Checkpoints, sealed evaluators, raw web captures, external research
checkouts, and complete experiment outputs remain in ignored artifact roots;
only redacted manifests and compact reports are publishable.

## License

AutoMarkov source code is licensed under the MIT License. Third-party code,
models, datasets, and benchmark assets retain their own licenses and are governed
by the reproduction manifests.
