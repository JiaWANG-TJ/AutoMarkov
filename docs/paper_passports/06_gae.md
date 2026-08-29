---
title: "High-Dimensional Continuous Control Using Generalized Advantage Estimation"
authors: "John Schulman, Philipp Moritz, Prafull Dhariwal, Michael Jordan, Pieter Abbeel"
year: 2016
method_id: "gae"
suite_ids: ["rl_single_agent"]
license: "ICLR 2017"
short: "Bias-variance tradeoff for advantage estimation via exponentially-weighted returns."
---
## Research Question
How can we systematically balance bias and variance when estimating advantage functions for policy gradient methods?

## Algorithm Summary
GAE (Generalized Advantage Estimation) introduces a single parameter lambda that interpolates between high-bias/low-variance one-step advantages (lambda=0) and high-variance/unbiased Monte Carlo returns (lambda=1). The exponentially-weighted average of n-step advantages provides a smooth continuum. Combined with a value function baseline, GAE yields low-variance advantage estimates suitable for policy gradient optimization.

## AutoMarkov Mapping
- Advantage estimation in `generation_methods.py` for baseline subtraction.
- Lambda parameter tuning affects training stability in `rllib_training.py`.
- Value function prediction connects to reward computation in `statistics.py`.

## Benchmarks
- MuJoCo locomotion tasks
- Continuous control benchmarks
- Simulated robotic manipulation

## Limitations
- Adds lambda hyperparameter requiring tuning per environment.
- Still relies on accurate value function approximation.
- No principled automatic lambda selection.