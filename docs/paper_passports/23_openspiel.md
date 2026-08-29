---
title: "OpenSpiel: An AI Framework for Games and Simulations"
authors: "Marc Lanctot, Edward Lockhart, Michal Lanctot, et al."
year: 2020
method_id: "openspiel"
suite_ids: ["multi_agent_systems"]
license: "Apache 2.0"
short: "C++/Python game framework for game-theoretic AI research."
---
## Research Question
How can we provide a unified framework for game-theoretic AI algorithms that works across diverse game types?

## Algorithm Summary
OpenSpiel is a research framework for reinforcement learning and game theory in games. It implements 25+ algorithms (minimax, CFR, Nash solvers, deep RL) across numerous game types (perfect information, imperfect information, simultaneous, turn-based). The C++ core with Python bindings supports games from Tic-Tac-Toe to Poker to Go. Standardized game descriptions enable algorithm comparison across game types.

## AutoMarkov Mapping
- Game types map to environment configurations in multi-agent setup.
- CFR/Nash solver integration relates to equilibrium computation in `generation_methods.py`.
- Imperfect information handling connects to partial observability in agent state models.

## Benchmarks
- Kuhn Poker, Leduc Poker, Go, Connect Four
- Hanabi cooperative card game
- Markov games (turn-based and simultaneous)

## Limitations
- C++ build setup can be complex.
- Python bindings may add overhead vs pure Python frameworks.
- Does not natively support continuous state/action spaces.