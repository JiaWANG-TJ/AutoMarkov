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

AutoMarkov is under active development. The current bootstrap milestone specifies
the domain contract, provenance rules, isolated runtime profiles, and executable
interface contracts; it does not claim that the Python package, CLI, or benchmark
matrix is implemented. The bootstrap tree intentionally has no installable Python
project. `pyproject.toml`, locked profiles, and package metadata are added with the
first source-bearing tracer bullet.

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
