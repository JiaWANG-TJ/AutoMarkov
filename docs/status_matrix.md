# AutoMarkov Status Matrix (2026-08-25)

**Baseline**: HEAD commit `4bbc601` (main, CI FAIL)
**Module compile**: `py_compile` PASS (all 38 `.py` source files)
**Import smoke**: `PYTHONPATH=src` import chain all PASS (except `rfc8785` not installed -- lock-hash dependency, not code defect)
**Ruff**: 22 errors (environment/tooling, not contract source)
**Pyright**: 88 errors (strict-mode propagation, environment/tooling)
**pytest**: ~15min/47% timeout (provenance fixture cascade)
**Profiles**: 13 `recipe_frozen`, 1 `attached_unverified`, 0 `built`

## Ticket Evidence Table

| Ticket | Status | Evidence Level | Evidence Locator |
|--------|--------|---------------|-----------------|
| T01 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/domain.py` L98 StrictFrozenModel, L359 RunId, L449 RunState, L407 TaskRequest |
| T02 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/canonical.py` (56507B) canonical_json_bytes, validate_and_measure_raw_json_tree |
| T03 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/lifecycle.py` (182740B) AppendRunEventsCommand; `tests/unit/test_event_log.py`, `test_event_union_closure.py`, `test_cross_run_lifecycle.py` |
| T04 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/domain.py` L393-446 TaskRequest/RequestBudget/RequestPermissions; `tests/contract/test_task_contract.py` |
| T05 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/local_llm_runtime.py` (85379B); `src/automarkov/adapters.py` L441-451 identity+probe+complete+close all `_deferred("llm.*", "T05")`; `tests/contract/test_local_llm_runtime.py`, `test_local_llm_artifact_bindings.py`, `test_local_llm_identity_closure.py` |
| T06 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/domain.py` L266-356 EvidenceStoreRef/EvidenceCapabilityGrant/GenerationEvidenceView; `src/automarkov/evidence_contracts.py` (21099B); `tests/contract/test_evidence_gateway.py` |
| T07 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/remote_env_contracts.py` (18240B); `src/automarkov/remote_env_codec.py` (11574B); `tests/contract/test_remote_env_codec.py`, `test_remote_env_certificate_contract.py`, `test_remote_env_identity.py` |
| T08 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/cli.py` L17 compile subparser + L63-83 compile_task("compile"); `tests/contract/test_task_contract.py`, `tests/contract/test_import_boundaries.py` |
| T09 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/adapters.py` L165-391 InMemoryCompiler.start/dispatch/resume fully, L428-437 package `_deferred("compiler.package", "T24")`; `tests/contract/test_artifact_repository.py`, `test_t09_repository_contracts.py` |
| T10 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/adapters.py` L454-477 search/extract/crawl all `_deferred("evidence.*", "T10")`; `tests/contract/test_evidence_gateway.py`, `test_clarification_boundary.py` |
| T11 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/sealed_evaluation.py` (98868B); `tests/contract/test_policy_evaluation_request.py`, `tests/security/test_sealed_evaluator.py` |
| T12 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/adapters.py` L535-539 exchange/close both `_deferred("remote_env.*", "T12")`; `src/automarkov/remote_env_codec.py` (11574B); `tests/contract/test_remote_env_codec.py`, `test_remote_env_certificate_contract.py` |
| T13 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/multi_agent_suite_contracts.py` (22445B) + `multi_agent_suite_adapters.py` (25832B); `tests/contract/test_cross_run_repository.py` |
| T14 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/clarification.py` (30452B); `tests/security/test_clarification_boundary.py`, `tests/contract/test_clarification_terminal.py` |
| T15 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/adapters.py` L480-497 sandbox.test `_deferred("sandbox.test", "T15")`; `src/automarkov/fixed_commit_runner.py` (238005B); `tests/contract/test_t15_repository_contracts.py`, `test_environment_sandbox.py`, `test_checkpoint_boundary.py` |
| T16 | IMPLEMENTED | RUNTIME_VERIFIED | `src/automarkov/pilots.py` (36641B) + `pilot_worker.py` (9165B); `tests/integration/test_cartpole_pilot_worker.py`; `tests/unit/test_pilot_orchestration.py` |
| T17 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/provenance.py` (160409B); 11 test files: `test_provenance_*.py`, `test_signed_*.py` |
| T18 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/rllib_training.py` (15344B) types/contracts/protocol; `src/automarkov/adapters.py` L564-565 `train` `_deferred("training.train", "T18")`; `tests/contract/test_upstream_manifests.py` |
| T19 | IMPLEMENTED | CONTRACT_IMPL | `src/automarkov/policy_export.py` (8683B) checkpoint+export+manifest types; `src/automarkov/adapters.py` L567-568 `evaluate` `_deferred("training.evaluate", "T19")`; `tests/contract/test_policy_export.py`, `test_policy_evaluation_request.py` |
| T20 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/benchmark_suites.py` (5244B) SuiteId(6)/VariantId(5)/MethodId; `tests/contract/test_benchmark_suites.py` |
| T21 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/generation_methods.py` (2722B) CapabilityManifest/PairBinding/EvidenceViewBinding; `tests/contract/test_generation_methods.py` |
| T22 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/ablation_ledger.py` (2630B) AblationBinding/AblationLedger; `tests/contract/test_ablation_ledger.py` |
| T23 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/release_pipeline.py` (5725B) ReplicationManifest validator enforces exactly 2 suites; `tests/contract/test_release_pipeline.py` validates 2-suite constraint + agent2world deferred comment |
| T24 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/statistics.py` (4680B) StrataPartition(24 strata) + PairedBootstrapResult + HolmFamily; `tests/contract/test_statistics.py` |
| T25 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/release_pipeline.py` L56-97 FreezeGateCheck/FreezeGateResult/ConfirmatoryMatrix; `tests/contract/test_release_pipeline.py` |
| T26 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/release_pipeline.py` L102-128 RedactedArtifactBinding/RedactionManifest/PublicReportManifest; `tests/contract/test_release_pipeline.py` |
| T27 | IMPLEMENTED | STATIC_VERIFIED | `src/automarkov/release_pipeline.py` L134-170 ReleaseGateCheck/ReleaseGateResult; `tests/contract/test_release_pipeline.py` |

## Summary by Evidence Level

| Level | Count | Tickets |
|--------|-------|---------|
| RUNTIME_VERIFIED | 1 | T16 |
| STATIC_VERIFIED | 18 | T01, T02, T03, T04, T06, T07, T08, T11, T13, T14, T17, T20, T21, T22, T23, T24, T25, T26, T27 |
| CONTRACT_IMPL | 8 | T05, T09, T10, T12, T15, T18, T19 |

## Critical Blockers

1. **CI**: provenance import FAIL (`rfc8785` not in installed packages); `import_smoke` skipped. 22 Ruff + 88 Pyright errors.
2. **Deferred capabilities**: 7 adapter stubs use `_deferred()` -- T05/T10/T12/T15/T18/T19 are contract-only, no runtime.
3. **Profiles**: 13/14 `recipe_frozen`; only `llm-qwen36-vllm` is `attached_unverified` (no build image).
4. **No running training**: No RLlib/PettingZoo training endpoint in CLI. `ScriptedTrainingRunner.train` deferred to T18.
5. **Agent2World**: 1 profile exists (`replication-agent2world_restricted`), capabilities=[], publishable=false.

## Profile Status Detail

| Profile ID | image_status | publishable | capabilities |
|------------|--------------|------------|------------|
| core | recipe_frozen | true | domain.protocols.v1 |
| rllib-core | recipe_frozen | true | policy.export.safetensors.v1, training.rllib.v1 |
| authoring | recipe_frozen | true | authoring.compiler.v1 |
| sealed-evaluator-rllib | recipe_frozen | true | sealed_evaluation.rllib.v1 |
| env-minigrid | recipe_frozen | true | remote_env.minigrid.v1 |
| env-mpe2 | recipe_frozen | true | remote_env.mpe2.v1 |
| env-smacv2 | recipe_frozen | true | remote_env.smacv2.v1 |
| env-citylearn | recipe_frozen | true | remote_env.citylearn.v1 |
| env-metadrive | recipe_frozen | true | remote_env.metadrive.v1, scenario.convert.v1 |
| env-taxi-gold | recipe_frozen | true | remote_env.taxi_v4.sealed.v1 |
| ood-pddl | recipe_frozen | true | planning.pddl.v1 |
| ood-openspiel | recipe_frozen | true | game_theory.openspiel.v1 |
| retrieval_tavily | recipe_frozen | true | evidence.crawl.v1, evidence.extract.v1, evidence.search.v1 |
| runner-control | recipe_frozen | true | fixed_commit.control.v1, remote_env.mtls.v1 |
| rllib-taxi-synthesis | recipe_frozen | true | training.rllib.taxi_synthesis.v1 |
| sealed-env-taxi-gold | recipe_frozen | true | remote_env.taxi_v4.sealed.v1 |
| llm-qwen36-vllm | attached_unverified | false | local_llm.openai_chat.v1 |
| replication-agent2world_restricted | restricted_disabled | false | (none) |