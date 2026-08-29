---
title: "Agent 2.0: Exploring the Architecture of Multi-Agent Systems"
authors: "Hong et al."
year: 2023
method_id: "agent2"
suite_ids: ["multi_agent_systems"]
license: "arXiv preprint"
short: "Systematic multi-agent system design combining LLMs with structured communication."
---
## Research Question
How should multi-agent systems be organized when agents are powered by large language models and need to collaborate on complex tasks?

## Algorithm Summary
Agent 2.0 proposes an architecture framework for LLM-based multi-agent systems. It categorizes agent interactions into workflow-driven, debate-driven, and hierarchy-driven patterns. The framework defines agent communication protocols, shared memory mechanisms, and role specialization strategies. Agents can be configured as generators, critics, evaluators, or orchestrators depending on task requirements.

## AutoMarkov Mapping
- Multi-agent architecture maps directly to SMAC-style team composition.
- Communication protocols relate to observation exchange in `generation_methods.py`.
- Role specialization connects to agent type selection in environment setup.

## Benchmarks
- Software engineering collaborative tasks
- Multi-agent debate for factuality
- Creative writing with iterative refinement

## Limitations
- Communication overhead scales poorly with agent count.
- Optimal architecture selection is task-dependent with no universal solution.
- LLM grounding errors compound across agents.