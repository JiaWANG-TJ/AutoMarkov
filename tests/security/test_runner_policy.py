from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from automarkov.fixed_commit_runner import (
    RUNNER_OUTPUT_SCANNER_RULES_HASH,
    ExecutionCapabilityPolicy,
    ExecutionMount,
    ExecutionMountPolicy,
    FixedCommitJobManifest,
    FixedCommitResourceLimits,
    MountAttestation,
    NetworkDecisionLog,
    OutputScannerPolicy,
    PhaseNetworkPolicy,
    RunnerNetworkDecision,
    RunnerPreflightError,
    validate_mount_profile_policy,
    validate_network_decisions,
    validate_worker_launch_policy,
)
from automarkov.lifecycle import ArtifactReference


@dataclass(frozen=True)
class _ProfileMountMaximum:
    read_mounts: tuple[str, ...]
    write_mounts: tuple[str, ...]


def test_runner_policies_are_closed_and_default_deny() -> None:
    network = PhaseNetworkPolicy.model_validate(
        {
            "schema_version": "automarkov.phase-network-policy.v1",
            "phase": "training",
            "egress_allowlist": (),
            "protocol_edges": ("RemoteEnv",),
            "gateway_principal_id": None,
            "deny_ip_literals": True,
            "deny_redirect_egress": True,
            "revoke_before_output_scan": True,
        },
        strict=True,
    )
    assert network.egress_allowlist == ()

    retrieval = network.model_copy(
        update={
            "phase": "retrieval",
            "egress_allowlist": ("api.tavily.com:443",),
            "protocol_edges": ("EvidenceGateway",),
            "gateway_principal_id": "principal_retrieval-tavily",
        }
    )
    PhaseNetworkPolicy.model_validate(retrieval.model_dump(), strict=True)

    invalid_network = deepcopy(retrieval.model_dump())
    invalid_network["egress_allowlist"] = ("127.0.0.1:443",)
    with pytest.raises(ValidationError):
        PhaseNetworkPolicy.model_validate(invalid_network, strict=True)

    with pytest.raises(ValidationError):
        FixedCommitResourceLimits.model_validate(
            {
                "schema_version": "automarkov.fixed-commit-resource-limits.v1",
                "phase": "analysis",
                "cpu_millis": 1000,
                "memory_bytes": 1024,
                "pids": 16,
                "io_bytes": 4096,
                "disk_bytes": 4096,
                "wall_time_ms": 1000,
                "gpu_devices": ("cuda:0",),
            },
            strict=True,
        )

    with pytest.raises(ValidationError):
        ExecutionMountPolicy.model_validate(
            {
                "schema_version": "automarkov.execution-mount-policy.v1",
                "candidate_worker": True,
                "mounts": (
                    {
                        "source_kind": "sealed_asset",
                        "source_id": "sealed_gold",
                        "target_path": "/mnt/automarkov/sealed/gold",
                        "access": "read_only",
                    },
                ),
            },
            strict=True,
        )

    capability = ExecutionCapabilityPolicy.model_validate(
        {
            "schema_version": "automarkov.execution-capability-policy.v1",
            "drop_all_capabilities": True,
            "allowed_capabilities": (),
            "no_new_privileges": True,
            "read_only_rootfs": True,
            "non_root": True,
            "seccomp_profile_hash": "sha256:" + "1" * 64,
            "apparmor_profile_hash": "sha256:" + "2" * 64,
            "apparmor_profile_name": "automarkov-" + "3" * 32,
        },
        strict=True,
    )
    assert capability.allowed_capabilities == ()

    scanner = {
        "schema_version": "automarkov.output-scanner-policy.v1",
        "scanner_id": "scanner_builtin",
        "scanner_version": "1.0.0",
        "scanner_rules_hash": RUNNER_OUTPUT_SCANNER_RULES_HASH,
        "reject_secrets": True,
        "reject_gold_markers": True,
        "reject_credential_locators": True,
    }
    OutputScannerPolicy.model_validate(scanner, strict=True)
    with pytest.raises(ValidationError, match="central rules identity"):
        OutputScannerPolicy.model_validate(
            scanner | {"scanner_rules_hash": "sha256:" + "0" * 64},
            strict=True,
        )

    reference = {
        "artifact_id": "artifact_" + "1" * 64,
        "payload_hash": "sha256:" + "2" * 64,
    }
    with pytest.raises(ValidationError):
        NetworkDecisionLog.model_validate(
            {
                "schema_version": "automarkov.network-decision-log.v1",
                "job_manifest": reference,
                "network_policy": reference,
                "denied_by_default": True,
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        MountAttestation.model_validate(
            {
                "schema_version": "automarkov.mount-attestation.v1",
                "job_manifest": reference,
                "mount_policy": reference,
                "exact_subset_verified": True,
            },
            strict=True,
        )

    for target_path in (
        "/mnt/automarkov/./input",
        "/mnt/automarkov/../../etc",
    ):
        with pytest.raises(ValidationError):
            ExecutionMount.model_validate(
                {
                    "source_kind": "input_artifact",
                    "source_id": "artifact_" + "a" * 64,
                    "target_path": target_path,
                    "access": "read_only",
                },
                strict=True,
            )


def test_mount_direction_and_network_decision_kind_are_mechanical() -> None:
    frozen_input = ArtifactReference(
        artifact_id="artifact_" + "1" * 64,
        payload_hash="sha256:" + "2" * 64,
    )
    manifest = FixedCommitJobManifest.model_construct(
        source_commit="a" * 40,
        input_artifacts=(frozen_input,),
        process_execution_id="process_policy",
        phase="training",
    )
    frozen_input_id = manifest.input_artifacts[0].artifact_id
    profile = _ProfileMountMaximum(
        read_mounts=("/mnt/automarkov/input",),
        write_mounts=("/mnt/automarkov/output",),
    )
    valid_mounts = ExecutionMountPolicy(
        schema_version="automarkov.execution-mount-policy.v1",
        candidate_worker=True,
        mounts=(
            ExecutionMount(
                source_kind="input_artifact",
                source_id=frozen_input_id,
                target_path="/mnt/automarkov/input",
                access="read_only",
            ),
            ExecutionMount(
                source_kind="output_root",
                source_id=manifest.process_execution_id,
                target_path="/mnt/automarkov/output",
                access="write_only",
            ),
        ),
    )
    validate_mount_profile_policy(profile, valid_mounts, manifest)

    for crossed_mount in (
        ExecutionMount(
            source_kind="input_artifact",
            source_id=frozen_input_id,
            target_path="/mnt/automarkov/output",
            access="read_only",
        ),
        ExecutionMount(
            source_kind="output_root",
            source_id=manifest.process_execution_id,
            target_path="/mnt/automarkov/input",
            access="write_only",
        ),
    ):
        with pytest.raises(RunnerPreflightError, match="mount direction"):
            validate_mount_profile_policy(
                profile,
                ExecutionMountPolicy(
                    schema_version="automarkov.execution-mount-policy.v1",
                    candidate_worker=True,
                    mounts=(crossed_mount,),
                ),
                manifest,
            )

    forged_source = ExecutionMountPolicy(
        schema_version="automarkov.execution-mount-policy.v1",
        candidate_worker=True,
        mounts=(
            ExecutionMount(
                source_kind="input_artifact",
                source_id="artifact_" + "f" * 64,
                target_path="/mnt/automarkov/input",
                access="read_only",
            ),
        ),
    )
    with pytest.raises(RunnerPreflightError, match="mount source"):
        validate_mount_profile_policy(profile, forged_source, manifest)

    training = PhaseNetworkPolicy(
        schema_version="automarkov.phase-network-policy.v1",
        phase="training",
        egress_allowlist=(),
        protocol_edges=("RemoteEnv",),
        gateway_principal_id=None,
        deny_ip_literals=True,
        deny_redirect_egress=True,
        revoke_before_output_scan=True,
    )
    validate_network_decisions(
        training,
        (
            RunnerNetworkDecision(
                decision_kind="control_edge",
                endpoint=None,
                protocol_edge="RemoteEnv",
                decision="allowed",
                reason_code="frozen_remote_env_edge",
            ),
        ),
    )

    for false_claim in (
        RunnerNetworkDecision(
            decision_kind="control_edge",
            endpoint=None,
            protocol_edge="EvidenceGateway",
            decision="allowed",
            reason_code="fabricated_edge",
        ),
        RunnerNetworkDecision(
            decision_kind="direct_egress",
            endpoint="api.tavily.com:443",
            protocol_edge=None,
            decision="allowed",
            reason_code="fabricated_endpoint",
        ),
    ):
        with pytest.raises(RunnerPreflightError):
            validate_network_decisions(training, (false_claim,))

    with pytest.raises(ValidationError):
        RunnerNetworkDecision.model_validate(
            {
                "decision_kind": "control_edge",
                "endpoint": "api.tavily.com:443",
                "protocol_edge": "RemoteEnv",
                "decision": "allowed",
                "reason_code": "ambiguous_claim",
            },
            strict=True,
        )


def test_candidate_role_overrides_self_reported_launch_policy() -> None:
    reference = ArtifactReference(
        artifact_id="artifact_" + "1" * 64,
        payload_hash="sha256:" + "2" * 64,
    )
    mount_policy = ExecutionMountPolicy(
        schema_version="automarkov.execution-mount-policy.v1",
        candidate_worker=False,
        mounts=(
            ExecutionMount(
                source_kind="sealed_asset",
                source_id=reference.artifact_id,
                target_path="/mnt/automarkov/sealed/gold",
                access="read_only",
            ),
        ),
    )
    network_policy = PhaseNetworkPolicy(
        schema_version="automarkov.phase-network-policy.v1",
        phase="sealed_evaluation",
        egress_allowlist=(),
        protocol_edges=(),
        gateway_principal_id=None,
        deny_ip_literals=True,
        deny_redirect_egress=True,
        revoke_before_output_scan=True,
    )

    with pytest.raises(RunnerPreflightError, match="candidate worker"):
        validate_worker_launch_policy("candidate", mount_policy, network_policy)


@pytest.mark.parametrize(
    ("phase", "expected_edges"),
    (
        ("authoring", ("LocalLlmRuntime",)),
        ("retrieval", ("EvidenceGateway",)),
        ("training", ("RemoteEnv",)),
        ("sealed_evaluation", ()),
        ("analysis", ()),
        ("export", ()),
    ),
)
def test_phase_protocol_edges_use_the_exact_closed_matrix(
    phase: str,
    expected_edges: tuple[str, ...],
) -> None:
    payload = {
        "schema_version": "automarkov.phase-network-policy.v1",
        "phase": phase,
        "egress_allowlist": (("api.tavily.com:443",) if phase == "retrieval" else ()),
        "protocol_edges": expected_edges,
        "gateway_principal_id": (
            "principal_retrieval-tavily" if phase == "retrieval" else None
        ),
        "deny_ip_literals": True,
        "deny_redirect_egress": True,
        "revoke_before_output_scan": True,
    }
    assert (
        PhaseNetworkPolicy.model_validate(payload, strict=True).protocol_edges
        == expected_edges
    )

    if phase != "retrieval":
        with pytest.raises(ValidationError, match="protocol"):
            PhaseNetworkPolicy.model_validate(
                payload | {"protocol_edges": ("EvidenceGateway",)},
                strict=True,
            )
