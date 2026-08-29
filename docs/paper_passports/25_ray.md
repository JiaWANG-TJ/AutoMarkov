---
title: "Ray: A Distributed Framework for Emerging AI Applications"
authors: "Philipp Moritz, Richard Liaw, Sanjiv Biswas, et al."
year: 2018
method_id: "ray"
suite_ids: ["rl_single_agent", "multi_agent_systems"]
license: "Apache 2.0"
short: "Distributed compute framework powering RLlib and large-scale ML."
---
## Research Question
How can we build a general-purpose distributed compute framework that supports both training and serving for emerging AI applications with dynamic task graphs?

## Algorithm Summary
Ray provides a distributed execution framework with a dynamic task graph API (remote functions and actors). It supports heterogeneous workloads combining stateless tasks and stateful services. Ray enables horizontal scaling across clusters with automatic resource scheduling, fault tolerance, and locality-aware execution. The framework underpins RLlib, Ray Tune (hyperparameter optimization), Ray Serve (model serving), and Ray Data (data processing).

## AutoMarkov Mapping
- Remote actors for rollout workers map to `rllib_training.py` distributed generation.
- Task graph execution relates to pipeline orchestration in `release_pipeline.py`.
- Resource scheduler connects to GPU allocation for training jobs.

## Benchmarks
- Distributed RL training scaling curves
- Hyperparameter sweep throughput (Ray Tune)
- Model serving latency (Ray Serve)

## Limitations
- Overhead of actor scheduling for fine-grained tasks.
- GIL and Python serialization costs limit per-task throughput.
- Cluster setup complexity beyond single-machine use cases.