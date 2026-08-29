---
title: "SafeTensors: A Universal Safe Serialization Format for Tensors"
authors: "Hugging Face team"
year: 2023
method_id: "safetensors"
suite_ids: ["rl_single_agent", "llm_agent"]
license: "Apache 2.0"
short: "Safe, fast tensor serialization format avoiding pickle vulnerabilities."
---
## Research Question
How can we serialize tensor data safely and efficiently without the security risks of Python pickle?

## Algorithm Summary
SafeTensors is a tensor serialization format that stores tensors in a flat binary format with JSON metadata. Unlike pickle-based formats, it cannot execute arbitrary code during loading, eliminating remote code execution vulnerabilities. The format is memory-mapped by design, enabling zero-copy loading and instant model startup. All major ML frameworks (PyTorch, TensorFlow, JAX) support SafeTensors. The format supports lazy loading (subset of tensors) and cross-framework tensor transfer.

## AutoMarkov Mapping
- Safe serialization relates to checkpoint security in `policy_export.py`.
- Memory-mapped loading connects to fast checkpoint restoration in `release_pipeline.py`.
- Cross-framework compatibility supports multi-backend inference pipelines.

## Benchmarks
- Loading time vs pickle safetensors comparison
- Security audit against pickle-based deserialization attacks
- Memory efficiency on large model checkpoints

## Limitations
- Does not support arbitrary Python objects (only tensors + metadata).
- Limited to contiguous tensor storage; sparse tensors require conversion.
- Ecosystem migration requires updating all model save/load code.