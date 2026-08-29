---
title: "CityLearn: A Benchmark Environment for Embodied Multi-Agent Communication"
authors: "Open-source collaborative project"
year: 2023
method_id: "citylearn"
suite_ids: ["multi_agent_systems", "simulation"]
license: "MIT"
short: "Embodied agents navigate and communicate in a procedurally generated city."
---
## Research Question
How can agents learn grounded communication while solving embodied navigation and collaboration tasks in a visually rich city environment?

## Algorithm Summary
CityLearn provides a procedurally generated 3D city environment where embodied agents must navigate, communicate, and collaborate to solve tasks such as treasure hunting, following instructions, and reaching designated locations. Agents receive egocentric visual observations and can send natural language messages to each other. The environment leverages Minecraft-like voxel worlds with realistic city layouts.

## AutoMarkov Mapping
- Embodied perception maps to visual observation preprocessing in `generation_methods.py`.
- Communication channel relates to agent message passing in multi-agent rollouts.
- Task completion rewards connect to team-level statistics in `statistics.py`.

## Benchmarks
- Treasure-hunt with communication
- Collaborative navigation in city maps
- Instruction following with grounding

## Limitations
- Visual observation complexity makes training challenging.
- Natural language communication requires LLM-based agents for meaningful interaction.
- Procedural generation quality varies; some layouts are unrealistic.