---
title: "RLlib: Industry-Grade Reinforcement Learning Library"
authors: "Eric Liang, Richard Liaw, Robert Nishihara, Philipp Moritz, Ken Holtby, et al."
year: 2023
method_id: "rllib"
suite_ids: ["rl_single_agent", "multi_agent_systems"]
license: "Apache 2.0"
short: "Scalable RL library with unified API and distributed training."
---
## Research Question
How can we build an industrial-strength RL library that scales from laptop to cluster while maintaining a unified API?

## Algorithm Summary
RLlib is an open-source RL library built on Ray that provides a unified policy class wrapping 20+ algorithms (PPO, SAC, DQN, DDPG, etc.). It supports distributed training via Ray actors, multi-GPU training, and resource-aware scheduling. RLlib supports single-agent, multi-agent (RLlib Multi-Agent), and model-based RL through a common policy/trainer/trainable interface. Checkpointing, hyperparameter tuning (via Ray Tune), and serving (via Ray Serve) are integrated.

## AutoMarkov Mapping
- Trainer class maps to `rllib_training.py` training loop.
- Policy abstraction connects to `policy_export.py` model export.
- Distributed rollout workers relate to parallel generation in `generation_methods.py`.

## Benchmarks
- Atari, MuJoCo, CartPole, custom environments
- Multi-agent PettingZoo and SMAC integration
- Production RL deployment at scale

## Limitations
- Complexity of API surface area for new users.
- Dependency on Ray ecosystem can complicate deployment.
- Debugging is harder in distributed settings.