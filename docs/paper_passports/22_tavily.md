---
title: "Tavily: AI-Optimized Web Search API"
authors: "Tavily Inc."
year: 2024
method_id: "tavily"
suite_ids: ["multi_agent_systems", "llm_agent"]
license: "Commercial"
short: "Web search API optimized for LLM agents with structured results."
---
## Research Question
How can LLM agents efficiently retrieve web information without the noise and ambiguity of raw search engine results?

## Algorithm Summary
Tavily provides a REST API that performs web search and returns results pre-processed for LLM consumption. It extracts clean content, removes boilerplate, condenses summaries, and structures output as JSON. The API supports search depth (basic vs advanced), domain filtering, and result deduplication. It integrates with LangChain, LlamaIndex, and direct HTTP call patterns used by autonomous agents.

## AutoMarkov Mapping
- Search results provide external knowledge grounding for agent perception in simulation.
- Structured JSON output maps to observation augmentation in `generation_methods.py`.
- Domain filtering relates to environment-specific knowledge retrieval.

## Benchmarks
- HotpotQA retrieval accuracy
- Agent task completion rates with vs without search
- Latency and cost comparison vs raw search engines

## Limitations
- Commercial API requires API key and network access.
- Web content quality varies; paywalled sites return limited content.
- Adds latency to agent decision loop; not suitable for real-time control.