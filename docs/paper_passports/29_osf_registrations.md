---
title: "OSF Registrations: Open Science Framework for Research Transparency"
authors: "Center for Open Science"
year: 2024
method_id: "osf_registrations"
suite_ids: ["multi_agent_systems"]
license: "CC-BY 4.0"
short: "Preregistration framework ensuring confirmatory research transparency."
---
## Research Question
How can researchers distinguish confirmatory from exploratory analysis and prevent post-hoc hypothesis fishing in empirical studies?

## Algorithm Summary
OSF Registrations allow researchers to publicly timestamp and archive their hypotheses, methods, and analysis plans before data collection or analysis. The registration includes a frozen document describing the research question, sample size, statistical tests, and decision criteria. Deviations from the registered plan are documented transparently. This practice separates confirmatory (registered) from exploratory (unregistered) analyses, strengthening evidential value. Major journals and funders increasingly require or incentivize preregistration.

## AutoMarkov Mapping
- Preregistration of methods connects to ablation study specification in `ablation_ledger.py`.
- Analysis plan documentation relates to benchmark protocol definition in `benchmark_suites.py`.
- Deviation tracking maps to experiment change logging in `provenance.py`.

## Benchmarks
- Reproducibility rates of preregistered vs non-preregistered studies
- Effect size inflation reduction in preregistered trials

## Limitations
- Cannot prevent all forms of p-hacking (flexible outcome reporting).
- Registration overhead deters small studies.
- Enforcing compliance requires journal-level policy.