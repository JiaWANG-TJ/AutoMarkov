# Decision D16

**Status**: OPEN
**Date Proposed**: 2026-08-25
**Decision Category**: Infrastructure / Build Authority

## Context

R05A-BUILD requires containerized builds for reproducible runtime images. The build host (where Docker/OCI images are assembled) and the container registry (where images are pushed) both represent trust boundaries. Scenarios include local development builds, CI-driven builds, and owner-initiated release builds. A decision is needed on who is authorized to trigger builds, which infrastructure hosts them, and where resulting images are stored.

## Recommended Option

Use GitHub Actions with pinned SHA for the build host and registry. All OCI builds are triggered by CI workflows pinned to exact action SHAs, preventing supply-chain drift. The resulting image is pushed to a registry authenticated via repository secrets and tagged with both the Git SHA and a semver label. No local Docker builds are permitted for release artifacts.

## Alternatives Considered

1. **Local `docker build` on developer machines** — fast but non-reproducible, depends on local tool versions, and produces unsigned images.
2. **Self-hosted build runner** — more control but requires owner to maintain and harden the runner infrastructure.
3. **Third-party build service (e.g., Google Cloud Build)** — offloads maintenance but introduces an external dependency and credential scope.

## Dependencies

This decision blocks R05A-BUILD. The build ticket cannot proceed until the build host and registry authority are confirmed.

## Owner Action Required

Owner must approve the GitHub Actions workflow, provision the registry credentials as repository secrets, and confirm the pinned SHA schedule for build actions.