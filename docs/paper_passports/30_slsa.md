---
title: "SLSA: Supply-chain Levels for Software Artifacts"
authors: "Google Security Team"
year: 2023
method_id: "slsa"
suite_ids: ["multi_agent_systems"]
license: "Apache 2.0"
short: "Framework for securing software supply chains with provenance levels."
---
## Research Question
How can we systematically prevent supply chain attacks and ensure software artifact integrity throughout the build and deployment pipeline?

## Algorithm Summary
SLSA (Supply-chain Levels for Software Artifacts) defines a graduated framework of security levels (L1-L3) for software build provenance. Each level adds requirements: L1 requires provenance documentation, L2 requires hosted build platform, L3 requires hardened non-falsifiable provenance. The framework addresses threats like unauthorized code modification, compromised build platforms, and dependency poisoning. SLSA provenance metadata documents the source, build process, and dependencies for each artifact.

## AutoMarkov Mapping
- Build provenance tracking maps to `provenance.py` pipeline tracking.
- Security levels relate to checkpoint integrity verification in `release_pipeline.py`.
- Dependency auditing connects to `policy_export.py` artifact documentation.

## Benchmarks
- Supply chain attack coverage across industry open-source projects
- Build reproducibility verification rates

## Limitations
- Higher levels require significant build infrastructure investment.
- Not all build systems support SLSA provenance natively.
- Provenance verification is still dependent on trusted hardware anchors.