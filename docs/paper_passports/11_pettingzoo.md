---
title: "PettingZoo: The Standard API for Multi-Agent Reinforcement Learning"
authors: "J K Terry, Benjamin Black, Megan Fegan, Andrew Porto, Marko Brguljac"
year: 2021
method_id: "pettingzoo"
suite_ids: ["multi_agent_systems"]
License: "MIT"
short: "Standard multi-agent RL environment API following Gymnasium conventions."
---
## Research Question
How can we provide a standardized, extensible API for multi-agent reinforcement learning environments that is compatible with the single-agent Gymnasium ecosystem?

## Algorithm Summary
PettingZoo defines an API standard for multi-agent environments with parallel and turn-based interaction modes. It provides environment wrappers, utility functions (wrappers, converters), and a curated set of reference environments covering cooperative, competitive, and mixed-motive scenarios. The API is designed for compatibility with Gymnasium, RLlib, and other single-agent frameworks by exposing per-agent observation/action spaces and using a dict-based step interface.

## AutoMarkov Mapping
- Multi-agent environments define the core simulation loop for `generation_methods.py`.
- Turn-based and parallel API modes relate to agent scheduling in rollout collection.
- Agent observation spaces map to the per-agent observation structure in `policy_export.py`.

## Benchmarks
- Classical multi-agent games (Prisoner Dilemma, etc.)
- Cooperative communication tasks
- Competitive pursuit-evasion scenarios

## Limitations
- Hidden state and partial observability handling varies across environments.
- No built-in curriculum learning or self-play scheduling.
- Some third-party environments have inconsistent PettingZoo compliance.