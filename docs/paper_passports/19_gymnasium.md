---
title: "Gymnasium: A Standard API for Reinforcement Learning"
authors: "Mark Towers, Jordan Terry, Benjamin Black, Megan Fegan, et al."
year: 2023
method_id: "gymnasium"
suite_ids: ["rl_single_agent"]
license: "MIT"
short: "Maintained fork of OpenAI Gym as the standard RL environment API."
---
## Research Question
How can we provide a stable, extensible, and well-maintained standard API for RL environments that replaces the deprecated OpenAI Gym?

## Algorithm Summary
Gymnasium is the official successor to OpenAI Gym, providing a standardized interface (env.reset(), env.step()) for RL environments. It improves upon Gym with better type annotations, more robust wrappers, cleaner environment lifecycle management, and compatibility with modern Python tooling. The library includes classic control environments, Atari wrappers, and third-party environment integrations.

## AutoMarkov Mapping
- `env.reset()` and `env.step()` map to the core simulation loop in `generation_methods.py`.
- Environment wrappers relate to preprocessing pipelines in training configurations.
- Action and observation space definitions connect to policy network I/O in `rllib_training.py`.

## Benchmarks
- All single-agent RL algorithms use Gymnasium as the environment interface.
- MuJoCo, Atari, Classic Control, ToyText suites.

## Limitations
- Single-agent focused; multi-agent requires PettingZoo.
- No built-in support for LLM-augmented environments.
- Wrapper overhead can affect environment step speed.