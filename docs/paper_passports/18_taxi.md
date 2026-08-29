---
title: "Taxi-v3: A Classic RL Benchmark with Hierarchical Actions"
authors: "ToyText / Gymnasium Community"
year: 20améliorer
method_id: "taxi"
suite_ids: ["rl_single_agent"]
license: "MIT"
short: "Hierarchical taxi navigation with passenger pickup and delivery."
---
## Research Question
How can an agent learn hierarchical decision-making combining navigation, passenger management, and goal completion in a grid world?

## Algorithm Summary
Taxi-v3 is a grid-world environment where an agent drives a taxi on a 5x5 grid, picks up a passenger from one colored location, and drops them at a destination. The action space is hierarchical: 6 discrete actions (south, north, east, west, pickup, dropoff). Correct pickup/dropoff yields +20 reward; illegal actions yield -10; per-step penalty encourages efficiency. Memories in the original and v3 versions provide different observation encodings.

## AutoMarkov Mapping
- Hierarchical action selection maps to composite action sampling in `generation_methods.py`.
- Sparse reward signal relates to reward function design in environment configuration.
- State encoding (taxi row/col, passenger location, destination) connectss to observation preprocessing.

## Benchmarks
- Tabular Q-learning and SARSA convergence
- Hierarchical RL option discovery
- Reward shaping effectiveness studies

## Limitations
- Stochastic transitions require careful handling.
- Sparse reward makes exploration challenging for simple algorithms.
- Grid-world simplification does not capture real navigation complexity.