---
title: "The StarCraft Multi-Agent Challenge"
authors: "Mikayel Samvelyan, Gregory Farquhar, Tom Schaul, Tim Rocktaschel"
year: 2019
method_id: "smac"
suite_ids: ["multi_agent_systems"]
license: "MIT"
short: "Cooperative multi-agent benchmark built on StarCraft II."
---
## Research Question
How can we benchmark and advance decentralized cooperative multi-agent reinforcement learning in complex partially observable real-time strategy environments?

## Algorithm Summary
SMAC (StarCraft Multi-Agent Challenge) provides a suite of cooperative micromanagement scenarios within StarCraft II. Agents control individual units with local observations (unit types, health, positions of allies/enemies) and must learn coordinated combat behaviors. The benchmark defines easy scenarios (symmetric battles) and hard scenarios (asymmetric, requiring specialized tactics like focus fire, kiting, and retreating).

## AutoMarkov Mapping
- Unit-level control maps to per-agent action spaces in multi-agent rollouts.
- Partial observability corresponds to local agent observations in `generation_methods.py`.
- Cooperative reward structure relates to team-level statistics in `statistics.py`.

## Benchmarks
- 23 cooperative micromanagement scenarios (SMAC v1/v2)
- Easy, hard, and super-hard difficulty levels
- Decentralized execution with centralized training (CTDE)

## Limitations
- StarCraft II dependency limits portability.
- Fixed map and enemy composition; generalization to new scenarios is not guaranteed.
- Does not test full macro-economic decision making.