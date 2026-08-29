---
title: "CartPole-v1: Classic Control Benchmark Environment"
authors: "OpenAI Gym / Gymnasium Community"
year: 2016
method_id: "cartpole"
suite_ids: ["rl_single_agent"]
license: "MIT"
short: "Balancing a pole on a moving cart; classic RL introductory environment."
---
## Research Question
How can an agent learn to balance a pole upright by applying left/right forces to a cart on a frictionless track?

## Algorithm Summary
CartPole is a canonical RL environment where a pole is attached by an unactuated joint to a cart moving on a frictionless track. The agent applies a binary force (left or right) to the cart. An episode ends when the pole angle exceeds 12 degrees, the cart moves off the track boundaries, or 500 steps are reached. Four continuous observations (cart position, cart velocity, pole angle, pole angular velocity) define the state space.

## AutoMarkov Mapping
- Binary action space maps to discrete `generation_methods.py` action sampling.
- Continuous observation space relates to state featurization in policy networks.
- Episode termination conditions connect to done-signal handling in training loops.

## Benchmarks
- Tabular Q-learning convergence test
- Policy gradient algorithm validation
- Value function baseline comparison

## Limitations
- Extremely simple dynamics; solved by most algorithms within minutes.
- Binary action space does not test continuous control.
- No partial observability or multi-agent extension.