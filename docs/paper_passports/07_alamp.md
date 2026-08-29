---
title: "A-LAMP: Adaptive Language-Augmented Model Predictive Control for Autonomous Driving"
authors: "Liu et al."
year: 2023
method_id: "alamp"
suite_ids: ["llm_agent", "simulation"]
license: "arXiv preprint"
short: "Language-augmented model predictive control combining LLM reasoning with classical planning."
---
## Research Question
How can large language models enhance model predictive control for better generalization in autonomous driving scenarios?

## Algorithm Summary
A-LAMP integrates LLM-based scene understanding with MPC-based trajectory planning. The LLM processes contextual descriptions (traffic rules, scene semantics) to condition the MPC cost function. This combination enables the planner to handle novel scenarios by leveraging language-based reasoning about driving norms and safety constraints.

## AutoMarkov Mapping
- LLM scene understanding maps to agent perception in multi-agent environments.
- MPC cost conditioning relates to reward shaping in `generation_methods.py`.
- Trajectory planning loop connects to rollout generation in `rllib_training.py`.

## Benchmarks
- CARLA autonomous driving simulator
- nuPlan real-world driving dataset
- Closed-loop driving evaluation scenarios

## Limitations
- LLM inference latency affects real-time planning performance.
- Requires high-quality natural language scene descriptions.
- MPC formulation assumes accurate dynamics model of ego vehicle.