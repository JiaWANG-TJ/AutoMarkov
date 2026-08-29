---
title: "Human-level Control through Deep Reinforcement Learning"
authors: "Volodymyr Mnih, Koray Kavukcuoglu, David Silver, Andrei A Rusu, Joel Veness, Marc G Bellemare, Alex Graves, Martin Riedmiller, Andreas K Fidjeland, Georg Ostrovski, et al."
year: 2015
method_id: "dqn"
suite_ids: ["rl_single_agent"]
license: "Nature 2015"
short: "First deep RL agent achieving human-level Atari performance."
---
## Research Question
Can a single deep neural network learn control policies directly from high-dimensional sensory input (raw pixels) using reinforcement learning?

## Algorithm Summary
Deep Q-Network (DQN) combines Q-learning with deep convolutional neural networks and introduces two key innovations: experience replay (storing transitions in a buffer and sampling mini-batches to break temporal correlation) and a target network (a periodically updated copy of the Q-network to stabilize bootstrapping targets). The agent processes stacked game frames through convolutional layers followed by fully connected layers to produce Q-values for each discrete action.

## AutoMarkov Mapping
- Experience replay buffer provides off-policy transition storage for DQN-style training.
- Target network stabilization relates to checkpoint management in `release_pipeline.py`.
- Exploration strategy (epsilon-greedy) interacts with `generation_methods.py` action sampling.

## Benchmarks
- Atari 2600 suite (49 games)
- Arcade Learning Environment (ALE)

## Limitations
- Limited to discrete action spaces.
- Overestimation bias in Q-value computation.
- No built-in mechanism for continuous action domains.
- Can be sample-inefficient for complex environments.