---
title: "Proximal Policy Optimization Algorithms"
authors: "John Schulman, Filip Wolski, Prafull Dhariwal, Alec Radford, Oleg Klimov"
year: 2017
method_id: "ppo"
suite_ids: ["rl_single_agent"]
license: "arXiv preprint"
short: "Clipped surrogate objective for stable policy gradient updates."
---
## Research Question
How can we perform reliable policy updates in trust region methods while maintaining simplicity and sample efficiency?

## Algorithm Summary
PPO introduces a clipped probability ratio objective that restricts policy updates to a trust region. The clip mechanism prevents excessively large gradient steps, enabling multiple epochs of minibatch updates from each data collection rollout. Two variants exist: PPO-Penalty (KL divergence penalty) and PPO-Clip (clipped surrogate). PPO-Clip became the dominant variant due to its simplicity and strong empirical performance.

## AutoMarkov Mapping
- Policy gradient computation in `generation_methods.py` for agent action sampling.
- RL training loop in `rllib_training.py` uses PPO as default algorithm.
- Baseline subtraction via GAE aligns with the clipped surrogate objective.

## Benchmarks
- MuJoCo continuous control tasks
- Atari discrete action benchmarks
- Roboschool locomotion tasks

## Limitations
- Can be less sample-efficient than off-policy methods like SAC.
- Requires careful tuning of clip ratio epsilon and number of epochs.
- On-policy nature limits data reuse compared to off-policy algorithms.