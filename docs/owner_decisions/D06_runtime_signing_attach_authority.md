# Decision D06

**Status**: OPEN
**Date Proposed**: 2026-08-25
**Decision Category**: Security / Runtime Authority

## Context

Ticket R05A-ATTACH requires the runtime to attach locally-hosted LLM services (vLLM, LlamaFactory, or other inference backends) to the AutoMarkov agent loop. Attaching an external service at runtime introduces a trust boundary: the system must verify that only owner-approved relay endpoints are connected and that payloads exchanged with the LLM service are tamper-evident. Without a signing mechanism, any process on the host could impersonate a legitimate attachment.

## Recommended Option

Owner provisions signing keys outside the repository (e.g., a sealed Kubernetes secret, an OS keyring entry, or an environment variable that is never committed). An approved relay service wraps the local LLM endpoint, validates the signing key on each inbound request, and forwards only signed payloads to the runtime. The repository ships relay configuration but never the signing key itself.

## Alternatives Considered

1. **Mutual TLS between runtime and LLM service** — strong transport security but requires certificate provisioning and rotation, which exceeds current operational maturity.
2. **Shared-secret in environment variable** — simple but vulnerable to exfiltration via process listing or crash dumps.
3. **No signing, rely on localhost binding alone** — insufficient when the host runs untrusted processes or container escapes.

## Dependencies

This decision blocks R05A-ATTACH. The attachment ticket cannot proceed until signing authority and key provisioning are settled.

## Owner Action Required

Owner must choose the key provisioning method (OS keyring, sealed secret, or vault integration) and approve the relay service configuration template.