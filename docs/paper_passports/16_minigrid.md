---
title: "MiniGrid: Multi-object Limitation RL Environment for Gym"
authors: "Maxime Chevalier"
year: 2018
method_id: "minigrid"
suite_ids: ["rl_single_agent", "multi_agent_systems"]
license: "MIT"
short: "Procedurally generated grid-world for multi-object RL benchmarking."
---
## Research Question
How can we provide fast, configurable grid-world environments for studying multi-object reasoning, instruction following, and goal-conditioned RL?

## Algorithm Summary
MiniGrid is a grid-world environment library where agents navigate a procedurally generated grid and interact with objects (doors, keys, balls, boxes). The observation is egocentric and partially observable. The action space is discrete (turn left, turn right, move forward, pickup, drop, toggle, done). Environments support mission-based, empty, and locked-door tasks. The lightweight Python implementation runs orders of magnitude faster than 3D simulators.

## AutoMarkov Mapping
- Grid-world state transitions map directly to environment step functions.
- Object interaction rules relate to action effects in `generation_methods.py`.
- Partial observability corresponds to egocentric observation masking.

## Benchmarks
- Empty rooms (navigation)
- Door and key (object interaction)
- Multi-room (instruction following)

## Limitations
- Simplified grid physics does not capture continuous spatial reasoning.
- Observation format is symbolic, not visual.
- Task complexity is limited by the grid resolution.