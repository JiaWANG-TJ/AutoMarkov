---
title: "Agent2World: An Open-source Multi-Agent Simulation Framework"
authors: "Community-driven open-source project"
year: 2024
method_id: "agent2world"
suite_ids: ["multi_agent_systems", "simulation"]
license: "Apache 2.0"
short: "Open-source framework for LLM-based multi-agent world simulation."
---
## Research Question
How can we provide a unified, extensible simulation environment for studying LLM-based agents interacting in shared worlds?

## Algorithm Summary
Agent2World is an open-source simulation framework designed for multi-agent systems with LLM-powered agents inhabiting a shared virtual environment. It supports configurable world physics, agent communication protocols, and diverse environment layouts. The framework provides APIs for agent creation, world state management, event logging, and evaluation pipelines. It enables researchers to study emergent social dynamics, collaboration, and competition among LLM agents.

## AutoMarkov Mapping
- Multi-agent world maps to the core simulation loop in `generation_methods.py`.
- World state transitions relate to environment step functions.
- Agent communication logging connects to `provenance.py` tracking.

## Benchmarks
- Emergent communication in multi-agent tasks
- Social simulation scenarios
- Collaborative problem-solving environments

## Limitations
- LLM inference costs dominate simulation budget.
- World physics fidelity limited compared to game-engine simulators.
- Scalability bottleneck in concurrent LLM API calls.