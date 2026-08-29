---
title: "vLLM: Efficient Memory Management for Large Language Model Serving"
authors: "Woosuk Kwon, Zhuohan Li, Siyuan Zhuang, Ying Sheng, et al."
year: 2023
method_id: "vllm"
suite_ids: ["multi_agent_systems", "simulation"]
license: "Apache 2.0"
short: "PagedAttention and continuous batching for high-throughput LLM serving."
---
## Research Question
How can we eliminate memory waste and improve throughput in serving large language models at scale?

## Algorithm Summary
vLLM introduces PagedAttention, a virtual memory-inspired technique for managing KV cache during transformer inference. Key-value blocks are stored in non-contiguous physical memory pages, eliminating memory fragmentation and enabling dynamic allocation. Continuous batching (also called iteration-level scheduling) aggregates requests at the iteration level rather than the request level, maximizing GPU utilization. These optimizations yield 2-4x throughput improvement over Hugging Face Transformers.

## AutoMarkov Mapping
- LLM inference throughput connects to agent response latency in multi-agent simulation.
- KV cache management relates to checkpoint I/O patterns in `release_pipeline.py`.
- Continuous batching maps to parallel environment stepping in rollout generation.

## Benchmarks
- Llama, OPT, LLaMA model families
- ShareGPT and Alpaca serving workloads
- Throughput benchmarks against HuggingFace, FasterTransformer

## Limitations
- Overhead of paging for short sequences can exceed benefits.
- Requires CUDA-compatible GPU; CPU inference not optimized.
- PagedAttention introduces slight numerical differences vs standard attention.
