---
title: "Soft Actor-Critic: Off-Policy Maximum Entropy Deep Reinforcement Learning with a Stochastic Actor"
authors: "Tuomas Haarnja, Abhishek Gupta, Sergey Levine"
year: 2018
method_id: "sac"
suite_ids: ["rl_single_agent"]
license: "ICML 2018"
short: "Maximum entropy RL framework balancing exploration and exploitation."
---
## Research Question
How can we unify entropy maximization with off-policy RL for stable continuous control with both sample efficiency and robustness?

## Algorithm Summary
SAC optimizes a maximum entropy objective that rewards both high return and high entropy, encouraging exploration. It uses twin Q-networks (clipped double Q-learning) to mitigate overestimation, a stochastic actor with squashed Gaussian policy, and automatic entropy tuning via a learned temperature parameter. The reparameterization trick enables gradients through stochastic sampling.

## AutoMarkov Mapping
- Stochastic policy sampling in `generation_methods.py` for continuous actions.
- Entropy-regularized reward computation relates to `statistics.py` logging.
- Twin Q-network pattern connects to checkpoint management in `policy_export.py`.

## Benchmarks
- MuJoCo continuous control tasks
- MetaWorld robotic manipulation
- D4RL offline RL benchmarks

## Limitations
- Entropy coefficient tuning adds complexity.
- Gaussian policy assumption may limit expressiveness for highly multimodal distributions.
- Can struggle with sparse reward environments without additional shaping.