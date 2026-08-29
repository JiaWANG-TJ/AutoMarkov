---
title: "NeurIPS Paper Checklist"
authors: "NeurIPS Conference Committee"
year: 2024
method_id: "neurips_checklist"
suite_ids: ["multi_agent_systems"]
license: "CC-BY 4.0"
short: "Standardized submission checklist for reproducibility and ethics in ML papers."
---
## Research Question
How can ML research submissions ensure reproducibility, ethical compliance, and reporting standards?

## Algorithm Summary
The NeurIPS checklist provides a set of mandatory and optional items that authors must address at submission time. It covers code availability, dataset documentation, compute resource reporting, experimental details, and ethics/broader impact discussion. The checklist enforces transparency about training compute, hyperparameter selection, and statistical significance of results. It has become a de facto standard across major ML conferences (ICML, ICLR, AAAI).

## AutoMarkov Mapping
- Code availability requirements map to checkpoint export in `policy_export.py`.
- Compute reporting relates to training metadata in `statistics.py`.
- Reproducibility checks connect to `provenance.py` tracking.

## Benchmarks
- Reproducibility rate of accepted papers pre/post checklist
- Ethics review compliance rates

## Limitations
- Self-reported compliance; no automated verification.
- Compute reporting is optional, reducing accountability.
- Does not cover dataset licensing in depth.