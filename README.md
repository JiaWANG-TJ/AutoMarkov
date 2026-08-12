# AutoMarkov

AutoMarkov is an evidence-grounded compiler for sequential decision problems.
It turns an approved natural-language task contract into a typed MDP, POMDP,
Markov game, or POSG specification, binds that specification to a verified
Gymnasium or PettingZoo environment, and evaluates policies through a frozen
RLlib protocol.

The project is designed around six deep public seams: `Compiler`,
`ArtifactRepository`, `LocalLlmRuntime`, `EvidenceGateway`, `ExecutionSandbox`,
and one environment-execution seam exposed through the narrow
`EnvironmentBinding`/`TrainingRunner` views. Generated artifacts are immutable and
content addressed; approvals and run transitions are append-only events.

## Status

AutoMarkov is under active development. The G0 core currently provides an
installable package, strict intake types, eight public protocol views, bounded
canonical JSON codecs, and immutable content-addressed artifact repositories
backed by either memory or transactional SQLite. It also provides the strict
append-only lifecycle reducer, specified-head replay, atomic terminal and
cross-run transactions, and a provenance catalog for 17 isolated runtime
profiles. Runtime integrations, suite adapters, training, and the benchmark
matrix remain tracked follow-up work.

## Development

Create the locked environment and run the focused trust-substrate checks:

```bash
uv sync --locked
uv run pytest -q tests/unit/test_canonical_json.py \
  tests/contract/test_artifact_repository.py \
  tests/contract/test_artifact_schema_registry.py
```

Verify the pinned upstream catalog, profile locks, SBOMs, license manifests,
Linux/amd64 selected artifacts, and isolation rules without installing the
profile-specific dependencies:

```bash
uv run automarkov verify-provenance --repository-root .
```

This metadata verification does not claim that a profile image or attached
runtime is ready. Profile-local import smokes, OCI image builds, and attached
service canaries are separate gates; only a verified build may acquire an OCI
image digest.

The current CLI walking skeleton accepts one request and returns a typed JSON view:

```bash
uv run automarkov compile \
  --request-id request_demo \
  --task-text "Model a finite-horizon inventory decision process."
```

## Runtime policy

- Generative inference uses a local Qwen3.6-35B-A3B vLLM service only.
- Tavily is restricted to Search, Extract, and Crawl with hosted answers disabled.
- Reinforcement learning uses PyTorch and RLlib through isolated dependency profiles.
- SwanLab is offline-first.
- Restricted research repositories and sealed evaluation assets are never vendored.

## Repository safety

Create `.env` from `.env.example`. The real file is ignored by Git and must have
mode `0600`. Checkpoints, sealed evaluators, raw web captures, external research
checkouts, and complete experiment outputs remain in ignored artifact roots;
only redacted manifests and compact reports are publishable.

## License

AutoMarkov source code is licensed under the MIT License. Third-party code,
models, datasets, and benchmark assets retain their own licenses and are governed
by the reproduction manifests.
