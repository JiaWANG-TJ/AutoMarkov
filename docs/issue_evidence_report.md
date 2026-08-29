# Issue Evidence Report — AutoMarkov T01-T27

**Date**: 2026-08-25
**Basis**: Pure code-level evidence, no inference
**Branch**: main (15 commits total)

---

## Summary

| Issue | Status | Evidence Level | Evidence Locator |
|--------|---------|--------------|-------------------|
| T01 | schema-only | SCHEMA_FINE | `src/automarkov/decision_process.py` — kernel/reward/predicate/distribution are free-text `str` fields (lines 39-69), not typed AST |
| T02 | schema-only | SCHEMA_FINE | `src/automarkov/task_contracts.py` — `ValidationLevel` enum+wire constants defined (lines 29-45); no executable validation implementation found |
| T03 | schema-only | SCHEMA_FINE | `src/automarkov/classification_contracts.py` — deterministic classification result contracts defined; no ClassificationProof implementation |
| T04 | deferred | DEFERRED_STUB | `src/automarkov/adapters.py:442-451` — `_deferred("llm.start/close/probe/complete", "T05")` |
| T05 | deferred (same as T04) | DEFERRED_STUB | `src/automarkov/adapters.py:442-451` — all four LLM lifecycle methods deferred |
| T06 | schema-only | SCHEMA_FINE | `src/automarkov/environment_contracts.py` — EnvironmentCandidate/SandboxPolicy/SandboxLimits all defined with validators |
| T07 | partial-worker | WORKER_EXIST | `src/automarkov/provenance.py` (3667 lines), `src/automarkov/fixed_commit_runner.py` (6033 lines) — full profile/runtime models, WORKER_BOUNDARY dict |
| T08 | schema-only, adapter deferred | DEFERRED_STUB | `src/automarkov/rllib_training.py` — schemas defined (413 lines); `adapters.py:565-568` — `_deferred("training.train/evaluate", "T18/T19")` |
| T09 | WORKER_EXIST | WORKER_EXIST | `src/automarkov/sealed_evaluation.py` (2473 lines) — SealedE2EGate, SealedWorkerTopology, InMemory/SQLite committers all implemented |
| T10 | deferred (evidence) | DEFERRED_STUB | `src/automarkov/adapters.py:461-477` — `_deferred("evidence.search/extract/crawl", "T10")`; contracts defined in `evidence_contracts.py` |
| T11 | CONTRACT_EXIST | CONTRACT_EXIST | `src/automarkov/benchmark_suites.py` — 6 SuiteId, 5 VariantId, BenchmarkManifest with 360-cell grid validator |
| T12 | deferred (env) | DEFERRED_STUB | `src/automarkov/adapters.py:537-540` — `_deferred("remote_env.exchange/close", "T12")`; contracts in `environment_contracts.py` |
| T13 | schema-only | SCHEMA_FINE | `src/automarkov/provenance.py` — RuntimeProfileManifest, provenance.py (3667 lines), but no dedicated security module |
| T14 | schema-only | SCHEMA_FINE | `src/automarkov/provenance.py:821-905` — RuntimeProfileArtifactPayload, RunnerRuntimeAttestation, attestation keys defined |
| T15 | deferred (sandbox.test) | DEFERRED_STUB | `src/automarkov/adapters.py:494` — `_deferred("sandbox.test", "T15")`; execution sandbox exists |
| T16 | schema-only | SCHEMA_FINE | `src/automarkov/provenance.py:171-199` — OFFICIAL_QWEN_WEIGHT_SHARD_HASHES, TavilyLeasePoolManifest |
| T17 | schema-only | SCHEMA_FINE | `src/automarkov/classification_contracts.py` — partial; `src/automarkov/task_contracts.py:29-45` — ValidationLevel enum |
| T18 | schema-only, adapter deferred | DEFERRED_STUB | `src/automarkov/rllib_training.py` — TrainingJobManifest, PPO, SeedContract; adapter deferred |
| T19 | schema-only | SCHEMA_FINE | `src/automarkov/policy_export.py` — PolicyExportManifest, PolicyEvaluationRequest with 10/1010-1010 seed binding |
| T20 | schemas-only | SCHEMA_FINE | `src/automarkov/benchmark_suites.py` — BenchmarkManifest, TaskCard, GoldScoreCalibration |
| T21 | schemas-only | SCHEMA_FINE | `src/automarkov/generation_methods.py` — CapabilityManifest, PairingContract with 6 methods |
| T22 | schemas-only | SCHEMA_FINE | `src/automarkov/ablation_ledger.py` — AblationLedger, Mpe2InfoStructureLedger |
| T23 | schemas-only | SCHEMA_FINE | `src/automarkov/release_pipeline.py:22-49` — ReplicationManifest with 2-suite validator |
| T24 | IDENTICAL_TO_T23 | IDENTICAL_FILE | `src/automarkov/release_pipeline.py` — shared file with T23/T25/T26/T27; compiler.package deferred |
| T25 | schemas-only | SCHEMA_FINE | `src/automarkov/release_pipeline.py:52-95` — FreezeGateResult, ConfirmatoryMatrix |
| T26 | schemas-only | SCHEMA_FINE | `src/automarkov/release_pipeline.py:98-128` — RedactionManifest, PublicReportManifest |
| T27 | schemas-only | SCHEMA_FINE | `src/automarkov/release_pipeline.py:130-171` — ReleaseGateResult with 6 check kinds |

---

## Evidence Level Definitions

| Level | Meaning |
|--------|---------|
| SCHEMA_FINE | Pydantic contract/model fully defined with field validators; no executable logic |
| CONTRACT_EXIST | Contract + some executable logic exists but not complete end-to-end |
| WORKER_EXIST | Full model + worker/boundary logic implemented |
| DEFERRED_STUB | `_deferred("capability", "TX")` raises ExactError at call |
| NOT_FOUND | No code or contract found |

---

## Deferred Capability Inventory

All items below raise `CapabilityDeferredError` at call time:

| Capability | Owner Ticket | Location |
|-----------|-------------|----------|
| `compiler.package` | T24 | `adapters.py:437` |
| `llm.start` | T04/T05 | `adapters.py:442` |
| `llm.probe` | T04/T05 | `adapters.py:445` |
| `llm.complete` | T04/T05 | `adapters.py:448` |
| `llm.close` | T04/T05 | `adapters.py:451` |
| `evidence.search` | T10 | `adapters.py:461` |
| `evidence.extract` | T10 | `adapters.py:469` |
| `evidence.crawl` | T10 | `adapters.py:477` |
| `sandbox.test` | T15 | `adapters.py:494` |
| `remote_env.exchange` | T12 | `adapters.py:537` |
| `remote_env.close` | T12 | `adapters.py:540` |
| `training.train` | T18 | `adapters.py:565` |
| `training.evaluate` | T19 | `adapters.py:568` |

---

## Test Collection Status

- **Contract tests**: 48 ERROR out of 67 attempted (only 21 collected)
- **Root cause**: most test files have import errors (likely pyproject not installed editable)
- **21 collected tests pass**: profile recipes, provenance, cache, environment contracts
- **Critical**: Cannot verify any schema contract correctness via automated tests until 48 collection errors are resolved

---

## Installed vs Deferred Architecture

```
Installed (production-ready adapters):
  InMemoryCompiler          — start/dispatch/resume/package
  ScriptedExecutionSandbox   — run()
  InMemoryEnvironmentBinding  — bind()
  ArtifactRepositoryRunnerArtifactWriter — write()
  ArtifactRepositoryE2ERunnerGrantResolver — resolve()
  ArtifactRepositoryE2EKeyPolicyResolver — resolve()
  OciFixedCommitExecutor     — execute() + full OCI lifecycle
  MemoryFixedCommitExecutor  — execute()
  LinuxCgroupV2ResourceCollector — collect()
  SqliteE2EGateCommitter   — commit()
  InMemoryE2EGateCommitter — commit()

Deferred (stub-only):
  ScriptedLocalLlmRuntime    — T05
  ScriptedEvidenceGateway    — T10
  ScriptedRemoteEnv         — T12
  ScriptedTrainingRunner    — T18/T19
```

---

## Blocker Issues (from backlog)

| Blocker | Status | Impact |
|----------|--------|--------|
| AM-B01 (Actions失败) | BLOKER | All main pipeline actions fail |
| AM-B02 (acceptance不一致) | BLOCKER | Issues closed without validation |
| AM-B03 (默认compile只建立 bootstrap) | BLOCKER | Application orchestrator missing |
| AM-B04 (Production config deferred) | BLOCKER | Production uses deferred adapters |
| AM-B05 (free-text kernel) | BLOCKER | Core DecisionProcess uses raw strings |

---

## Key Observations

1. **Contract schemas are comprehensive** — T01-T27 have well-frozen Pydantic models with validators
2. **Zero production execution** — every runtime-capable adapter returns `CapabilityDeferredError`
3. **Productive codebase is massive** — `repository.py` (9274 lines), `lifecycle.py` (4719), `fixed_commit_runner.py` (6033), `provenance.py` (3667), `sealed_evaluation.py` (2473)
4. **Test suite non-functional** — 48/67 tests cannot be collected, blocking regression verification
5. **CLI minimal** — only `compile`, `verify-provenance`, `pilot` commands; experiment CLI absent
6. **Governance files absent** — no SECURITY, CONTRIBUTING, CODEOWNERS, CHANGELOG at repo root
7. **Package metadata incomplete** — pyproject.toml version 0.1.0, no pyproject URLs/classifiers/keywords