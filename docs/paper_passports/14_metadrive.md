---
title: "MetaDrive: An Open-ended Driving Simulator for Generalizable Multi-Agent Learning"
authors: "Li et al."
year: 2022
method_id: "metadrive"
suite_ids: ["multi_agent_systems", "rl_single_agent"]
license: "Apache 2.0"
short: "Procedurally generated driving scenarios for multi-agent RL generalization."
---
## Research Question
How can we train driving agents that generalize across diverse traffic scenarios rather than memorizing fixed maps?

## Algorithm Summary
MetaDrive procedurally generates driving scenarios with configurable traffic density, road structures, and vehicle behaviors. It provides Gymnasium-compatible environments for both single-agent and multi-agent driving tasks. The simulator supports customizable vehicle dynamics, sensor configurations, and reward functions. Training across the procedural distribution encourages agents that handle novel configurations at test time.

## AutoMarkov Mapping
- Procedural scenario generation maps to curriculum sampling in training loops.
- Vehicle dynamics simulation relates to environment step in `generation_methods.py`.
- Multi-agent traffic interactions connect to PettingZoo-style agent management.

## Benchmarks
- Waymo-style real-world driving datasets
- Closed-loop driving evaluation
- Multi-agent traffic negotiation

## Limitations
- Domain gap between 2D/top-down physics and real driving.
- Procedural generation may miss rare but critical edge cases.
- No built-in support for map-level semantic understanding.