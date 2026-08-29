---
title: "LlamaFactory: Unified Efficient Fine-Tuning and Inference Platform"
authors: "hiyouga et al."
year: 2024
method_id: "llamafactory"
suite_ids: ["multi_agent_systems", "simulation"]
license: "Apache 2.0"
short: "Efficient framework for LLM fine-tuning with diverse training and inference backends."
---
## Research Question
How can we unify the fragmented landscape of LLM fine-tuning and inference tools into one compatible framework?

## Algorithm Summary
LlamaFactory provides a unified platform for LLM fine-tuning (SFT, RLHF, DPO, PPO, ORPO) and inference (vLLM, transformers backends). It supports QLoRA, LoRA, and full parameter training with multi-GPU and distributed training. The platform integrates with Hugging Face model hub and supports 100+ architectures. A YAML-based configuration system simplifies reproducible experimentation.

## AutoMarkov Mapping
- LLM fine-tuning backends connect to the training pipeline architecture.
- LoRA adapters relate to efficient checkpoint management in `policy_export.py`.
- Inference backends (vLLM) map to agent perception module.

## Benchmarks
- Llama, Qwen, Gemma, Mistral model families
- MMLU, C-Eval, ARC, HellaSwag evaluation
- Alpaca and ShareGPT dialogue datasets

## Limitations
- Requires significant GPU memory for full parameter training.
- Multi-node setup complexity for large models.
- RLHF integration is still evolving.