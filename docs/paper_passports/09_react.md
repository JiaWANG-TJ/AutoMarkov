---
title: "ReAct: Synergizing Reasoning and Acting in Language Models"
authors: "Shunyu Yao, Jeffrey Zhao, Dian Yu, Nan Du, Izhak Shafran, Tianao Yu, Yuan Cao"
year: 2023
method_id: "react"
suite_ids: ["multi_agent_systems", "llm_agent"]
license: "ICLR 2023"
short: "Interleaving chain-of-thought reasoning with tool-use actions."
---
## Research Question
How can language models alternate between reasoning traces and concrete actions to solve tasks requiring both thought and interaction?

## Algorithm Summary
ReAct generalizes ReAct prompting to support flexible interleaving of reasoning thoughts and tool-use actions. The agent generates a thought, takes an action (e.g., search, calculate, read), observes the result, and continues reasoning. This synergy avoids the pitfalls of pure reasoning (hallucination, error compounding) and pure acting (no planning). ReAct serves as a general framework applicable to any task requiring both reasoning and external tool interaction.

## AutoMarkov Mapping
- Thought-action-observation cycle maps to the agent perception-action loop.
- Tool interfaces correspond to environment API calls in simulation setup.
- Chain-of-thought traces relate to multi-step action sequences in `generation_methods.py`.

## Benchmarks
- HotpotQA multi-hop question answering
- FEVER fact verification
- AlfWorld interactive decision making

## Limitations
- Prompt engineering is sensitive; poor prompts degrade action quality.
- No inherent mechanism for backtracking or undoing wrong actions.
- Requires reliable external tools; tool failure cascades through the reasoning chain.