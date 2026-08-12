from __future__ import annotations

import os
import re
import stat
import subprocess
import tomllib
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Annotated, BinaryIO, Literal, NamedTuple, Self, cast
from urllib.parse import unquote, urlsplit

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
from packaging.markers import (
    InvalidMarker,
    Marker,
    UndefinedEnvironmentName,
    default_environment,
)
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version
from pydantic import AfterValidator, Field, computed_field, model_validator

from automarkov.canonical import (
    MAX_CANONICAL_DOCUMENT_BYTES,
    FrozenSequence,
    FrozenStringMapping,
    canonical_json_bytes,
    parse_json_payload,
    validate_and_measure_raw_json_tree,
)
from automarkov.domain import StrictFrozenModel

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_RESOURCE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,127}$"
_PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,127}$"
_RELATIVE_FILE_PATTERN = r"^[A-Za-z0-9._/-]+$"
_PYTHON_VERSION_PATTERN = r"^3\.(?:10|11|12)\.[0-9]+$"
_EXACT_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9.+-]*)?$"
_LICENSE_REF_PATTERN = re.compile(r"\bLicenseRef-[A-Za-z0-9.-]+\b")
_GLIBC_MINOR = 36
_LICENSE_REF_EVIDENCE: dict[str, tuple[str, str]] = {
    "LicenseRef-NVIDIA-CUDA-EULA": (
        "full_text",
        "sha256:565d1bdf61ba7cda4cac8e2ddc9151da47958d0040be2395991201a998e2ecdd",
    ),
    "LicenseRef-NVIDIA-NVSHMEM-SDK-SLA": (
        "full_text",
        "sha256:1f5b7ada702926bc73327e6eb02dc2d41facc844cc4512ac900451bda06a459e",
    ),
    "LicenseRef-NVIDIA-Proprietary": (
        "upstream_short_reference",
        "sha256:cadbd9ec42d8720863721bf443e1236eb3665eaba1f1f144fe1a662068547fb9",
    ),
    "LicenseRef-NVIDIA-SOFTWARE-LICENSE": (
        "upstream_short_reference",
        "sha256:df7937c88667a2bc38edb70cb4a366873ab77d9433f9f5f15d0ddd13a1dc6668",
    ),
    "LicenseRef-NVIDIA-cuDNN-SLA": (
        "full_text",
        "sha256:f2491d96c29cc9ea45eee3c9a9f739ee48ec4497dab2de859a9b7aef9aa60654",
    ),
    "LicenseRef-Public-Domain": (
        "full_text",
        "sha256:5c225a4c708ed835e620178ba1856fcbf65480bc9c610ab70661ea21cf8f190c",
    ),
}

# Manifest 是外部 JSON wire contract；这里保留严格 scalar，避免在 raw ingress 前构造领域 RootModel。
Digest = Annotated[str, Field(strict=True, pattern=_DIGEST_PATTERN)]
GitCommit = Annotated[str, Field(strict=True, pattern=_COMMIT_PATTERN)]
ResourceId = Annotated[str, Field(strict=True, pattern=_RESOURCE_ID_PATTERN)]
RuntimeProfileId = Annotated[str, Field(strict=True, pattern=_PROFILE_ID_PATTERN)]
ArtifactReferenceId = Annotated[
    str,
    Field(strict=True, pattern=r"^artifact_[0-9a-f]{64}$"),
]


def _require_relative_file(value: str) -> str:
    path = Path(value)
    if (
        path.is_absolute()
        or not value
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("manifest paths must be normalized repository-relative files")
    return value


def _require_exact_version(value: str) -> str:
    try:
        parsed = Version(value)
    except InvalidVersion as error:
        raise ValueError("version must be a valid PEP 440 release") from error
    if str(parsed) != value:
        raise ValueError("version must use its canonical PEP 440 spelling")
    return value


RelativeFile = Annotated[
    str,
    Field(strict=True, pattern=_RELATIVE_FILE_PATTERN),
    AfterValidator(_require_relative_file),
]
ExactVersion = Annotated[
    str,
    Field(strict=True, pattern=_EXACT_VERSION_PATTERN, max_length=128),
    AfterValidator(_require_exact_version),
]
NonEmptyText = Annotated[str, Field(strict=True, min_length=1, max_length=4_096)]

RedistributionPolicy = Literal[
    "permitted",
    "download_only",
    "research_evaluation_only",
    "prohibited",
]
InstallMode = Literal["pip", "git_submodule", "external_cache", "dataset_download"]
ResolutionStatus = Literal["blocked_unresolved", "external_restricted", "pinned"]
IntegrationStatus = Literal[
    "active",
    "attached_unverified",
    "blocked_unresolved",
    "deferred",
    "reference_only",
    "restricted_disabled",
]
ImageStatus = Literal[
    "attached_unverified",
    "built",
    "recipe_frozen",
    "restricted_disabled",
]
PlatformId = Literal["linux/amd64"]

_PROFILE_FILES = frozenset(
    {
        ".dockerignore",
        "Containerfile",
        "license-manifest.json",
        "profile.json",
        "pyproject.toml",
        "sbom.spdx.json",
        "smoke.json",
        "uv.lock",
    }
)
_RESTRICTED_TOKEN = b"agent2" + b"world"
_RESTRICTED_NAME = _RESTRICTED_TOKEN.decode("ascii")
_RESTRICTED_PROFILE_ID = "replication-agent2" + "world-restricted"
_RESTRICTED_COMMIT = b"1330f3cde9509f05d204a255f0f7f432" + b"08515dce"
_RESTRICTED_PATH = b"external/" + b"restricted"


class _RestrictedSourcePolicy(NamedTuple):
    resource_id: str
    markers: tuple[bytes, ...]
    encoded_markers: tuple[bytes, ...]


class _GitIndexPayload(NamedTuple):
    relative_path: str
    payload: bytes


_BASE64_CANDIDATE = re.compile(
    rb"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{16,}={0,2}(?![A-Za-z0-9+/_=-])"
)
_PUBLISH_TREE_IGNORED_DIRECTORIES = frozenset(
    {
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
    }
)
_REPOSITORY_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".cache",
        ".hypothesis",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".scratch",
        ".swanlab",
        ".venv",
        ".vscode",
        ".worktrees",
        "__pycache__",
        "swanlog",
        "venv",
        "wandb",
    }
)
_REPOSITORY_IGNORED_ROOTS = frozenset(
    {
        "artifacts",
        "build",
        "checkpoints",
        "dist",
        "external",
        "private",
        "references/checkouts",
        "secrets",
    }
)
_REPOSITORY_PROHIBITED_FILE_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".key",
    ".log",
    ".pem",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
)
_TRACKED_IGNORED_ROOT_SENTINELS = frozenset(
    {
        "artifacts/.gitignore",
        "external/.gitignore",
    }
)


def _is_sensitive_publish_path(relative_path: str) -> bool:
    name = relative_path.rsplit("/", maxsplit=1)[-1].lower()
    return (
        name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name.endswith(_REPOSITORY_PROHIBITED_FILE_SUFFIXES)
    )


def _is_prohibited_tracked_publish_path(relative_path: str) -> bool:
    if relative_path in _TRACKED_IGNORED_ROOT_SENTINELS:
        return False
    if any(
        relative_path == ignored_root or relative_path.startswith(f"{ignored_root}/")
        for ignored_root in _REPOSITORY_IGNORED_ROOTS
    ):
        return True
    parts = relative_path.split("/")
    if any(part in _REPOSITORY_IGNORED_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    return parts[:1] == ["benchmarks"] and any(
        part in {"gold", "sealed"} for part in parts[1:]
    )


_RESTRICTED_DECLARATION_FILES = frozenset(
    {
        "AGENTS.md",
        "docs/AutoMarkov_complete_development_specification.md",
        "docs/experiments/automarkov-code-experiment-plan.md",
        "docs/research/2026-08-09-upstream-foundations.md",
        "docs/research/2026-08-10-t04-upstream-bom-and-profile-isolation.md",
        "references/manifest.yaml",
        "tests/contract/test_provenance_full_identity_review.py",
        "tests/contract/test_provenance_native_final_review.py",
        "tests/contract/test_provenance_native_identity_hardening.py",
        "tests/contract/test_provenance_native_policy_review.py",
        "tests/contract/test_provenance_native_publish_tree_closure.py",
        "tests/contract/test_provenance_native_review_closure.py",
        "tests/contract/test_provenance_review_regressions.py",
        "tests/contract/test_runtime_profiles.py",
        "tests/contract/test_upstream_manifests.py",
    }
)
_RESTRICTED_DECLARATION_HASHES = {
    "AGENTS.md": "sha256:e519c90660e223fa482fbb593a5fca5eb30d195dffdb00ce01dcee2a9f824e17",
    "docs/AutoMarkov_complete_development_specification.md": "sha256:45388f0ab0325dbca0ef36a12fbf7a2a29ea464a793d6cd9379722da8aa2f82d",
    "docs/experiments/automarkov-code-experiment-plan.md": "sha256:4ecb3df476a21b7520cca09665823ed36f74091a15e663e075ffce569d940fda",
    "docs/research/2026-08-09-upstream-foundations.md": "sha256:fa61637cc2e04c376e3864dba701db4763d21265dad264279fffaa021199ade5",
    "docs/research/2026-08-10-t04-upstream-bom-and-profile-isolation.md": "sha256:02de50f0f3c5fa7f4687c013d60848e051e5abcc0391507702e39422bb74868d",
    "references/manifest.yaml": "sha256:c5842873d6e4eb669afb654bcf03ef3437901e1e5ac9d9d8754b782618349103",
    "tests/contract/test_provenance_full_identity_review.py": "sha256:0196058913d3fcdbed9fea4562ab0f94196b8990a51e907a945ae517910f74b6",
    "tests/contract/test_provenance_native_final_review.py": "sha256:8655329ed487b46ae98faaa30e7aae3fa8ff3966b6183e819ef29a897082aabe",
    "tests/contract/test_provenance_native_identity_hardening.py": "sha256:3d7cac86d5969c0662cc413b99a776d54a1f6605ab1480482696cdf7d63c3b01",
    "tests/contract/test_provenance_native_policy_review.py": "sha256:edc072c6fe8753bfbb5c0ed8e57e04712f53bdc205d871b8fdc691979396a08c",
    "tests/contract/test_provenance_native_publish_tree_closure.py": "sha256:d0528fe1c7d2647784d42c07e30ce28da5ca529a97ed2ffffc322f581a11efe4",
    "tests/contract/test_provenance_native_review_closure.py": "sha256:9232c62222cffe35df9425357f50c915af3bbdba6d2108a294b29d1f625bcaec",
    "tests/contract/test_provenance_review_regressions.py": "sha256:86ee58ba6db12674260f06f6d8fe67e39c0caaca822fc9040a3c00c19327ecf5",
    "tests/contract/test_runtime_profiles.py": "sha256:f6ab410db42fc7f1740d315fd63842329ddd0fa6fda6a9e6a024669b4556fa56",
    "tests/contract/test_upstream_manifests.py": "sha256:50aa07b5c70ed17d04073f6c19f8479f3a3959219317026d84a9d429d6a46b58",
}
_REGISTERED_SOURCE_HASHES = {
    "docs/research/2026-08-12-t05-local-qwen-vllm-evidence-boundary.md": "sha256:8d834623ff11dedee861e2265b5ed88b385b0ca8f07351254ac15b13fa24f216",
    "pilots/cartpole_cpu_smoke.v1.json": "sha256:f5033d7f17aa86ca11434a2cbb5487daef77ae805f567a6ca3e0408ff21b05cd",
    "src/automarkov/__init__.py": "sha256:4b223b666b0f828e897ca12d79e4fc7c6d7e9f66df456072346893ad6caad06a",
    "src/automarkov/__main__.py": "sha256:fa7fea7c3e4993d12329136ac82abe6017f845deebc12a41c3cdf9f1fcf4f3b3",
    "src/automarkov/adapters.py": "sha256:8740b40d036f166f80f402fe8ed8a0512914c2d1b0f25f8a7bf33ad3329c28d5",
    "src/automarkov/api.py": "sha256:02ff865035b1fd0ef7c4db07f4d532fbf7ea25fd2cfe8e4b7dd228e62cb7fe57",
    "src/automarkov/canonical.py": "sha256:ab2a9d8b073d0360519bdccdbb9193fd05a84cc93ebc3c7d3ca7b089f6714390",
    "src/automarkov/clarification.py": "sha256:c573bbe7c0e11a2ea3d6f11518777e3703e9d8aef0738a078603012f76cfa497",
    "src/automarkov/classification_contracts.py": "sha256:bd998086eeb8960ca3392d3adedec44bed76b5031059a51505fa2781d28b8430",
    "src/automarkov/cli.py": "sha256:46b36b52fb648801fb048d461aff001f3ae4a6640ccd87375a1343400b7d9cac",
    "src/automarkov/decision_process.py": "sha256:5151fc0f36d3c780b0c234fbeeca729266eb8beda77dc094b33d66a16d80dcaa",
    "src/automarkov/domain.py": "sha256:a8ba211a38ff51aa9790919eba6e80f088c4a46047677263768a576f5d7c36d2",
    "src/automarkov/environment_contracts.py": "sha256:213e23416e5f3e8c7225fac8c0f2d774906a9c15651f4481b48c549bbf57e09a",
    "src/automarkov/environment_implementation.py": "sha256:76865dd2d1921b617ff7b1223ef22d035c2e983aa9b3db187e0066ed73b1a332",
    "src/automarkov/environment_sandbox.py": "sha256:141ec69991a6b0c7816ee3c807c1ef7c5088ef4b5833a6fc793620f5d3171675",
    "src/automarkov/errors.py": "sha256:60e84a4a8eb4f1b557d29a5fcd76c4c01863a05eee5224f7f92165c8449973b8",
    "src/automarkov/evidence_access.py": "sha256:54fb806fbffcdaae0a74d063be0340f604fd86a5df93eefd9af660e3f48149ef",
    "src/automarkov/evidence_contracts.py": "sha256:146968213ca04ff71738de016bc73040b7c34066e42a77f4a21855d39a6e074b",
    "src/automarkov/lifecycle.py": "sha256:dfbc4340770e0f07a82bad0920fdc8d8977c4371c89392692f3be952989609f2",
    "src/automarkov/llm_contracts.py": "sha256:ed28778321b0602af86f1194d8995f0186a697d796596948771ba88046ede8e0",
    "src/automarkov/local_llm_runtime.py": "sha256:fe65afa1c8cc8c3741a051cb0e38e8bb28271bbb40a242eae02678facb6a8155",
    "src/automarkov/multi_agent_suite_adapters.py": "sha256:1d16b3eac4fe6e6943d83ee5f6a2153855cc62dd561706a037f8eb0667259a60",
    "src/automarkov/multi_agent_suite_contracts.py": "sha256:c259b933fbf2b4bcbdeb5f2aca46fd2f38bcf6013416e19c5f4c72d6ff9b6d11",
    "src/automarkov/pilot_worker.py": "sha256:b839852a077b8a0061dbc607717d390c2cfb2a9faf15c38706d3390e734df76f",
    "src/automarkov/pilots.py": "sha256:248abfa58835912e1360ac3d4e047e4986bdb99be9d3208b754f2f320e7f1295",
    "src/automarkov/public.py": "sha256:fd8a9e0a1103a2adb05f3629c6383f59e7dcab8f6f35e99e74d487865407bcb1",
    "src/automarkov/public_validation.py": "sha256:07c63b575ec1c8f55f7b690f24d11270a2fb4d21db70ad7ac9486763938a0f5c",
    "src/automarkov/remote_env.py": "sha256:bb1eefb023922d95a6022262af0da1a5e6a853d5ea067bac8c79e56835d4f156",
    "src/automarkov/remote_env_codec.py": "sha256:3c513da59a3d746dbb4f8a9a6f1a50e05cce45ea9f407ab76f0f13642d971bfe",
    "src/automarkov/remote_env_contracts.py": "sha256:d297614501120a83150660bd42f119a8baa4e2d9df15f92f61abcb1093e28d3c",
    "src/automarkov/repository.py": "sha256:5392eac482f9591b398d853989351b5609b6b2cfb5010c6b87c15b78a1192fbe",
    "src/automarkov/suite_adapters.py": "sha256:f483b3eb5adbf88a9e6e12da8b876c9163549a284c9602c0bffe82f9cb0221c5",
    "src/automarkov/task_contracts.py": "sha256:da1d46f12d7eddc78bc298f6eb18e5a15d28552f4f670752b0e4cf98290914da",
    "src/automarkov/tavily_gateway.py": "sha256:a252ddc6f37e42c460ce15c3485a9d8bcf0f9a16675e627c7d00c6f8fed25f1e",
    "src/automarkov/validation_contracts.py": "sha256:863f78b66320b5e6cb3bb0a1a3fc5055c6233022a8e4b93505c56b4bb698e0be",
    "tests/contract/test_ablation_gates.py": "sha256:730311dd85f17a9980f34734def8d5061738065a3b671801b089284f9e596260",
    "tests/contract/test_alternative_exact_parents.py": "sha256:693a3cb21c9d6e3045fe5b6b21a7557c090a7f37eaf83bd9934736c74a726e3b",
    "tests/contract/test_artifact_repository.py": "sha256:fa103ec239b3d2fd25e9ca5d0d7af0d7c75e5bfc88e5c826ff4f310d8451ba0e",
    "tests/contract/test_artifact_schema_registry.py": "sha256:dd59ea3c903da3e679eaeaa22d144bf5f9a6f636a70ca8ac8b31801ebf4d6300",
    "tests/contract/test_cartpole_remote_env_slice.py": "sha256:d4cc706359feb40b2783b9af4958fb572121394a39a9c87364cc44f4366682c4",
    "tests/contract/test_clarification_terminal.py": "sha256:a595c9635686934ae68fee90784f1ffe8631bbec93fa5eaaf62e1db723ef39c1",
    "tests/contract/test_classification.py": "sha256:1565770422adf62125c77daa76d8d9ff3492d08883e1e65f231f9ed0c950abd3",
    "tests/contract/test_cross_run_repository.py": "sha256:9e76078815e0ea0d92f97b4a52b726ae3a2250edad1a3067ee1011b266df4c77",
    "tests/contract/test_evidence_gateway.py": "sha256:ee9842c2cc5437670caeddcea3b612ce804daaff669c9f81a180c3f3856813c4",
    "tests/contract/test_formal_invariants.py": "sha256:5bac17227ae78fe8deaff2c039953757830f5dd8c12dbf4eabfb49ed26aa6cc1",
    "tests/contract/test_implementation_route.py": "sha256:d996740b48ee7e9ded1276e4bb7c39cd327e8881fd3a30f7e9a32bcab49b140a",
    "tests/contract/test_import_boundaries.py": "sha256:7659d75be497c03194afcc32d6e7fd1fc25f680fcc0944d4e4c94b4e175955be",
    "tests/contract/test_local_llm_artifact_bindings.py": "sha256:f8dbef37186bbcbf0063606a37940c479da82d890498362d69688828ac075607",
    "tests/contract/test_local_llm_identity_closure.py": "sha256:6a71bcb996cb349fce3d03cc0c6a89290e9b7039cb07fee5cedea9319cc24c21",
    "tests/contract/test_local_llm_runtime.py": "sha256:ace4ce2d181f62ae33fb607c16bd7898ac0579fbc3be49317038b9638d15a787",
    "tests/contract/test_ood_handoff.py": "sha256:3a8b5ae21cb7390b7593dc81b3c76e702546a7f399e2d73ff01a6daa980044e4",
    "tests/contract/test_pilot_manifest.py": "sha256:d424a4dbfa2e86062c05a937f2219c4f198f61993354364f3c464a7f5a7543cc",
    "tests/contract/test_privileged_runtime_connection_provider.py": "sha256:0498c028a17636b665e27c33257602858d2048ec0f8222b2450e480718cc6e67",
    "tests/contract/test_profile_recipe_workflow.py": "sha256:bdf040ca001d8adc1d2e8164a36993662f776394059e07a844697eeb43b49499",
    "tests/contract/test_provenance_full_identity_review.py": "sha256:0196058913d3fcdbed9fea4562ab0f94196b8990a51e907a945ae517910f74b6",
    "tests/contract/test_provenance_native_final_review.py": "sha256:8655329ed487b46ae98faaa30e7aae3fa8ff3966b6183e819ef29a897082aabe",
    "tests/contract/test_provenance_native_identity_hardening.py": "sha256:3d7cac86d5969c0662cc413b99a776d54a1f6605ab1480482696cdf7d63c3b01",
    "tests/contract/test_provenance_native_license_smoke_review.py": "sha256:8150c5841d05deb214c5decd82a02c5140ca7dda70f3986298d6d24507e3e087",
    "tests/contract/test_provenance_native_policy_review.py": "sha256:edc072c6fe8753bfbb5c0ed8e57e04712f53bdc205d871b8fdc691979396a08c",
    "tests/contract/test_provenance_native_publish_tree_closure.py": "sha256:d0528fe1c7d2647784d42c07e30ce28da5ca529a97ed2ffffc322f581a11efe4",
    "tests/contract/test_provenance_native_review_closure.py": "sha256:9232c62222cffe35df9425357f50c915af3bbdba6d2108a294b29d1f625bcaec",
    "tests/contract/test_provenance_native_upstream_review.py": "sha256:88cb8a5a11bfff30c761bb7c593b0fd60cd8afa2e5e7ff9eeb7667087a1a8115",
    "tests/contract/test_provenance_review_regressions.py": "sha256:86ee58ba6db12674260f06f6d8fe67e39c0caaca822fc9040a3c00c19327ecf5",
    "tests/contract/test_public_seams.py": "sha256:4649536b3762eb2eefc39f7a8ce35eb1d98568b2de2cb32fbf3c4cc5bef51433",
    "tests/contract/test_reduction_lineage.py": "sha256:a9da22195c3da227e7738a2236a67718b30be8e6e700bc616ce0603b840c55d4",
    "tests/contract/test_remote_env_certificate_contract.py": "sha256:22b028f2708a5bdc07353b0916ec3bc017feb9902a3eb035ced7853c2b4a5c13",
    "tests/contract/test_remote_env_codec.py": "sha256:262e4cefcc6f504909346742b52d72b2ab4f5ce675a75d0382b92d00954dce22",
    "tests/contract/test_run_lifecycle.py": "sha256:81847e00acf5fa8bc2ac16f45ad1226363b2373e41798507d84f0cb32b943762",
    "tests/contract/test_runtime_profiles.py": "sha256:f6ab410db42fc7f1740d315fd63842329ddd0fa6fda6a9e6a024669b4556fa56",
    "tests/contract/test_signed_approval.py": "sha256:5ecc3e6df49eae73821770fa0905682ce0691e7cb723fd84ceb1dc70cace5d4f",
    "tests/contract/test_signed_audit_security.py": "sha256:44094e70316cd22b57d22cd79174782761d70dfd60c201b44f67bd93e2c0a5e3",
    "tests/contract/test_t09_repository_contracts.py": "sha256:b5762059c232611409fe910b52e49b0a5758c408f32803d3a03ddd229a3c092c",
    "tests/contract/test_t15_repository_contracts.py": "sha256:93c9dafcaae909449e72b7a3137793cc8fa8ace1bc3e4e1dc4d13a540f509cdf",
    "tests/contract/test_task_contract.py": "sha256:4b76062073435c522203b5648b37026b5b885a9a489474046e3f9bd2e4a6f438",
    "tests/contract/test_upstream_manifests.py": "sha256:50aa07b5c70ed17d04073f6c19f8479f3a3959219317026d84a9d429d6a46b58",
    "tests/contract/test_validation_claims.py": "sha256:7e3925a4905ca386aa4a0f348b4007ad04e2436f1a2a3b582856468e984249fe",
    "tests/end_to_end/test_compiler_walking_skeleton.py": "sha256:4502b0c12d062d85a33bde49acbc1fc6a3537679db89c73f7b1be1639b281f1d",
    "tests/end_to_end/test_environment_slice.py": "sha256:8f18ad27e8f081f4fd004202d9b0a7c1d95336ca3073a01eba38db4e454e1616",
    "tests/integration/test_cartpole_pilot_worker.py": "sha256:71c6ded2bae74eeeb039d1579918f3bc7f497c75d4a61acd3fbb67652013a112",
    "tests/security/test_clarification_boundary.py": "sha256:d020ddb09d683325623abe0dfc661730eba92d0427e2bd6a275a90e846acd2af",
    "tests/security/test_environment_sandbox.py": "sha256:21fd3a688c2599c291a51727e2bdc288b65255f6d4538faa5b44e24b62810634",
    "tests/security/test_evidence_tiers.py": "sha256:07892d79ecf9c2f77b1912dd4721cebb0f543e48281247c4dc3de7959330502e",
    "tests/security/test_feedback_routing.py": "sha256:f8ae36dc66abe288dafdcff6b7196990e8c463fde876941c9a39e04b554fed01",
    "tests/security/test_remote_env_identity.py": "sha256:8096291e9418aeaffe6aedec1a709a231286216aacaa8426f56bf8e60ad535b1",
    "tests/security/test_remote_env_transport.py": "sha256:858218fccb3f6bac4a09d419777262b5a4e2b883f8426072a0b5e341c9d95988",
    "tests/security/test_secret_redaction.py": "sha256:c136d1c273701c04efa01e8b9917b5b1a86f7a315f45bb2d4797f6026e496832",
    "tests/suites/test_citylearn_adapter.py": "sha256:5b205f6ff4712409b45bc24a34eeb4e3c82f988bc61255c049cf569a8bb59835",
    "tests/suites/test_metadrive_adapter.py": "sha256:236227c7383b4ab8644e9e87803af4f8248e93edebedf4a5345d37debff455bf",
    "tests/suites/test_minigrid_adapter.py": "sha256:a25a0f37500a555c5d84f47e576c1e15f52affd9e1437ae035d69753f8fd8bd7",
    "tests/suites/test_mpe2_adapter.py": "sha256:02e39d0331196b11ae2c551ac97a03a69148390238d21eed0e1cbbe627ea7c89",
    "tests/suites/test_smacv2_adapter.py": "sha256:50f4af1c84e121a5f74641b5106a416d2da663470426d924f6a732464a77ba09",
    "tests/suites/test_taxi_adapter.py": "sha256:311eb7627d2f49fcc4ce5eede832aaeea1caca7409aaa9046058e4360463dae8",
    "tests/unit/test_canonical_json.py": "sha256:a804b4466ff360a28f765fe7cb19f40f5e1b4b858b4eca010f650d2121e86448",
    "tests/unit/test_cross_run_lifecycle.py": "sha256:eb67b194a350190bf7397e937e31d1a829cb7b8a2bbc2eb858920fb2d9b23c4f",
    "tests/unit/test_decision_process_ir.py": "sha256:ad644c920a8d387eb5ab49d8bc2a7ae9483c65dcc2f5b21eacade8f7ae6ecd29",
    "tests/unit/test_event_log.py": "sha256:93e4c6eadf9ebaae299aaccd5ce6577e0a6a3dee28f141e9a4b61eef5641b8d3",
    "tests/unit/test_event_union_closure.py": "sha256:6bcaeccf5b428e386007dc1e986ce173fc10fe6a21847e6a8e15a9a9edbb9340",
    "tests/unit/test_pilot_orchestration.py": "sha256:82814e8737dcdefbed1fb6ceeb07d8cd0930c01fab4f885bc52e5c7df49d6fdb",
    "tests/unit/test_projection_identity.py": "sha256:79b037b033486931c696b250dc2086ece6b34f8172a0cf78e912bfd0e575f6bb",
    "tests/unit/test_provenance_extra_closure.py": "sha256:d9d97406805392888d74b45088e5a85d2e70b5ede59cfccfc9690af545ce70df",
    "tests/unit/test_reducer.py": "sha256:e6cbb0f1aa67be5bde07c61bca4665aa14d47bdaca3c311b8a82288870f813c7",
    "tests/unit/test_tavily_routing.py": "sha256:e58e7c5847c8d6cbae7d56f076a105abcff7be667fb3bb336bafe4cc1c65c374",
    "tests/validation/test_public_ladder.py": "sha256:9f4cfac1d3118155e7b652528d5ae15ae5cff4d1ba41aafc2e3bb6048be4f939",
}
_REGISTERED_DISTRIBUTABLE_FILES = frozenset(
    {
        ".env.example",
        ".github/workflows/provenance.yml",
        ".gitignore",
        "AGENTS.md",
        "CONTEXT.md",
        "LICENSE",
        "README.md",
        "docs/AutoMarkov_complete_development_specification.md",
        "docs/adr/0001-immutable-artifacts-and-append-only-events.md",
        "docs/adr/0002-isolated-runtime-profiles.md",
        "docs/adr/0003-sealed-evaluation-boundary.md",
        "docs/agents/domain.md",
        "docs/agents/issue-tracker.md",
        "docs/agents/triage-labels.md",
        "docs/experiments/automarkov-code-experiment-plan.md",
        "docs/research/2026-08-09-upstream-foundations.md",
        "docs/research/2026-08-10-t04-upstream-bom-and-profile-isolation.md",
        "pyproject.toml",
        "references/manifest.yaml",
        "src/automarkov/provenance.py",
        "src/automarkov/py.typed",
        "uv.lock",
    }
) | frozenset(_REGISTERED_SOURCE_HASHES)
_ALLOWED_UNREGISTERED_PROFILE_TEXT_FILES = frozenset({"operator-notes.txt"})
_APPROVED_REGISTRY_URLS = frozenset({"https://pypi.org/simple"})
_APPROVED_ARTIFACT_HOSTS = frozenset({"files.pythonhosted.org"})
_APPROVED_GIT_HOSTS = frozenset({"github.com"})
_APPROVED_UPSTREAM_HOSTS = _APPROVED_GIT_HOSTS | {"huggingface.co"}
_UV_REQUIRED_VERSION = "==0.11.16"
_BUILD_BACKEND_REQUIREMENTS = ("setuptools==84.0.0",)
_SOURCE_BUILD_PACKAGES = {
    "authoring": ("google-search-results",),
    "env-citylearn": ("tinynumpy",),
    "env-metadrive": ("progressbar", "scenarionet"),
    "env-smacv2": ("mpyq", "s2protocol", "smacv2"),
}
_UV_IMAGE = (
    "ghcr.io/astral-sh/uv:0.11.16@"
    "sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d"
)
_BOOKWORM_GLIBC_BASES = {
    "3.10.18": (
        "python:3.10.18-slim-bookworm@"
        "sha256:b4d66d07136c546f1765eae2bfcce9a64fa95f37c717c02bedd06d0476d1dbbd"
    ),
    "3.11.13": (
        "python:3.11.13-slim-bookworm@"
        "sha256:cec9aa7aa96eea4fa036e9b82be1e6b325f2e3707f462d885868df51ec0a4b47"
    ),
    "3.12.11": (
        "python:3.12.11-slim-bookworm@"
        "sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49"
    ),
}
_BOOKWORM_GIT_INSTALL = (
    "RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources "
    "&& printf '%s\\n' 'deb [check-valid-until=no] "
    "https://snapshot.debian.org/archive/debian/20250910T000000Z "
    "bookworm main' > /etc/apt/sources.list.d/debian-snapshot.list "
    "&& apt-get update && apt-get install -y --no-install-recommends "
    "ca-certificates=20230311+deb12u1 git=1:2.39.5-0+deb12u2 "
    '&& test "$(git --version)" = "git version 2.39.5" '
    "&& test -s /etc/ssl/certs/ca-certificates.crt "
    "&& rm -rf /var/lib/apt/lists/*"
)
_BASE_FORBIDDEN_PACKAGES = frozenset(
    {_RESTRICTED_NAME, "camel-oasis", "llamafactory", "swanlab"}
)
_ISOLATED_PACKAGE_OWNERS = {
    "camel-ai": frozenset({"authoring"}),
    "citylearn": frozenset({"env-citylearn"}),
    "metadrive-simulator": frozenset({"env-metadrive"}),
    "open-spiel": frozenset({"ood-openspiel"}),
    "ray": frozenset({"rllib-core", "rllib-taxi-synthesis", "sealed-evaluator-rllib"}),
    "scenarionet": frozenset({"env-metadrive"}),
    "smacv2": frozenset({"env-smacv2"}),
    "tavily-python": frozenset({"retrieval-tavily"}),
    "unified-planning": frozenset({"ood-pddl"}),
    "vllm": frozenset({"llm-qwen36-vllm"}),
}

_PROFILE_CAPABILITIES = {
    "authoring": ("authoring.compiler.v1",),
    "core": ("domain.protocols.v1",),
    "env-citylearn": ("remote_env.citylearn.v1",),
    "env-metadrive": ("remote_env.metadrive.v1", "scenario.convert.v1"),
    "env-minigrid": ("remote_env.minigrid.v1",),
    "env-mpe2": ("remote_env.mpe2.v1",),
    "env-smacv2": ("remote_env.smacv2.v1",),
    "llm-qwen36-vllm": ("local_llm.openai_chat.v1",),
    "ood-openspiel": ("game_theory.openspiel.v1",),
    "ood-pddl": ("planning.pddl.v1",),
    _RESTRICTED_PROFILE_ID: (),
    "retrieval-tavily": (
        "evidence.crawl.v1",
        "evidence.extract.v1",
        "evidence.search.v1",
    ),
    "rllib-core": ("policy.export.safetensors.v1", "training.rllib.v1"),
    "rllib-taxi-synthesis": ("training.rllib.taxi_synthesis.v1",),
    "runner-control": ("fixed_commit.control.v1", "remote_env.mtls.v1"),
    "sealed-env-taxi-gold": ("remote_env.taxi_v4.sealed.v1",),
    "sealed-evaluator-rllib": ("sealed_evaluation.rllib.v1",),
}

_PROFILE_LICENSE_MANIFEST_HASHES = {
    "authoring": "sha256:0395b7a86dad7e34f3f909ebad921b379ac0311991b3e3b334e9d6cfc619a1a0",
    "core": "sha256:fca7358e5986776c1a953f17553d6628581ed7f1ff454a93c9dd9aa9f0328241",
    "env-citylearn": "sha256:c8654ae4ae81a9fe041676f5eb238450bafe6481ec3c21acc9bd4aac74b6e90b",
    "env-metadrive": "sha256:3e5d3b3833ff5a9bab93fe86a2ba940c5ff6dbf6ed612dd87f816c94bde80aa7",
    "env-minigrid": "sha256:bb22481dd6c8e352dcb868e2d9dd5f502a8694e31eb69710bc9b887cd78901c1",
    "env-mpe2": "sha256:c8c890d94d1747818c4b8699d24923da48a9a634f23a4e6666650f1cb8c58d38",
    "env-smacv2": "sha256:a3b70af4252c05fd1dadd5b1f7277e4acc2bf37b43d3cf3f79d9b25a85e1dc8b",
    "llm-qwen36-vllm": "sha256:725b7645e573d6aeedeaf4101cb9d5db077e73cf28b40ac7263d06cf4d2d0b31",
    "ood-openspiel": "sha256:ba6acda739dfb182e139b09fee2f06350c6a5d28c3ddecfa0e4ca48bb95cd98a",
    "ood-pddl": "sha256:cc7df878f54fda7cfc95d24a3dfd7f420fe5ac121f5b8436a8ae1fa92e5edb45",
    _RESTRICTED_PROFILE_ID: "sha256:97957bc91e104b31692b7faf6973d9b13e37e093c20889a9ed12782288d6d43a",
    "retrieval-tavily": "sha256:5a63b0cbcd6ec024b3412297732f0706003e38cac37da55dd862a6e6657fb319",
    "rllib-core": "sha256:a0a56d15353c10f7e7523d67e534d5f46777c9eaf69e431a5f038daffea1ef7d",
    "rllib-taxi-synthesis": "sha256:18995e74d7b62aabc458bf80e607f269ef0e34a29faba4fff6bd31a01a88a4ae",
    "runner-control": "sha256:59de8d118928b91612d60e31d241f97b1abdbf278f44e1ed90ab607fcd3bb5a0",
    "sealed-env-taxi-gold": "sha256:3b3426a852f7a93a72eaf0754f50ab2c3d4649fa27bc08ce57e27f2f7f0b3186",
    "sealed-evaluator-rllib": "sha256:b138619e1d1f5b82c216dfb790f9c4d6312f04882ea01fa87a91629bb16b6c81",
}

_ACTIVE_UPSTREAM_IDENTITIES = {
    "camel-ai": (
        "https://github.com/camel-ai/camel",
        "deb286f36702ab15a2cb890c6e223a79e4ce4284",
        "sha256:950deb34b1341a0ac95236fae92fe247c318c3a83a62c9ebacbe1882530ab1f6",
    ),
    "citylearn": (
        "https://github.com/citylearn-project/CityLearn",
        "29062af6d077409e1c37a3e53a6cac30fd4d02bc",
        "sha256:5a136b692e5288cfc83099df5f21d4dc6ebbb20303ceaf7116f231158c333ea3",
    ),
    "gymnasium": (
        "https://github.com/Farama-Foundation/Gymnasium",
        "a923da5d4415a1aa5195d99341069da5e16deed7",
        "sha256:7dacaa9772e856aee6943b32ef663d3634d91d72ec7bbc74d136943673f91e18",
    ),
    "gymnasium-1-3": (
        "https://github.com/Farama-Foundation/Gymnasium",
        "53bf3e9a884783eb72ad3fc8b15780914c97c3e1",
        "sha256:7dacaa9772e856aee6943b32ef663d3634d91d72ec7bbc74d136943673f91e18",
    ),
    "metadrive": (
        "https://github.com/metadriverse/metadrive",
        "5bf8ea8909c4643a4099a250e6f5fb89c695d8b4",
        "sha256:45f65910a340942a8bdcd995c3703fc0f7cba6e5ae195d488ba1ab65c60dec2b",
    ),
    "minigrid": (
        "https://github.com/Farama-Foundation/Minigrid",
        "90928729376741a41222a257911343b97103b548",
        "sha256:6c2915ffe9ac7ad36b26a36d03c2297ccc42a3dd914c902b28bfd5ff08c21b7c",
    ),
    "mpe2": (
        "https://github.com/Farama-Foundation/MPE2",
        "7590d9d52791e321974d4fda6090fb18f34dbf49",
        "sha256:0a918bb2373ebaba541cfc55270ab24151f4e594a03d51d8eb513c57fab1b814",
    ),
    "open-spiel": (
        "https://github.com/google-deepmind/open_spiel",
        "112b77704631fc2ce7ad8e4581f6ca09798ce15a",
        "sha256:cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
    "pettingzoo": (
        "https://github.com/Farama-Foundation/PettingZoo",
        "1756a4d7494b532651f0024ff7087ef4945432a6",
        "sha256:57569ca4221c4cbf9a035d1280d142550b7021722a70ffd79c318ae382689cc4",
    ),
    "pydantic": (
        "https://github.com/pydantic/pydantic",
        "a7928e692e5a7841c4379d1af1fd37966941dade",
        "sha256:a9e186f3ca16b5eef84318e7a701721351a00cb7b8ae3a4394b67b49e3529ef3",
    ),
    "ray-rllib": (
        "https://github.com/ray-project/ray",
        "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a",
        "sha256:cc68f9a408c8edf33c900f645846a7d8388a23e4b92a4a9fce7499c372b2acc0",
    ),
    "safetensors": (
        "https://github.com/huggingface/safetensors",
        "a406ca3e7a90598be0cd05a50069cb9bf5ef6ba6",
        "sha256:c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    ),
    "scenarionet": (
        "https://github.com/metadriverse/scenarionet",
        "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170",
        "sha256:c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    ),
    "smacv2": (
        "https://github.com/oxwhirl/smacv2",
        "577ab5a2cff2391f8df582da5731ea9cd6adf3c6",
        "sha256:6debad0d199caa25baac65c7f963d507370dc360daba2ba043a36e08a7afc145",
    ),
    "tavily-python": (
        "https://github.com/tavily-ai/tavily-python",
        "de924695765d5cf28bd1975c1cfca0cd07cd7005",
        "sha256:5487dae77c2e475439bd62828b6c5e4896e79f3f7bcc1dbec10efc59fc8bb77f",
    ),
    "torch": (
        "https://github.com/pytorch/pytorch",
        "cf30153c4c131c8164ee7798e5022d810682e2cb",
        "sha256:bd018feef8825e88181c84eb7e3aa4eafb8f08a20d9fd6ef948569610c4a3e43",
    ),
    "unified-planning": (
        "https://github.com/aiplan4eu/unified-planning",
        "42e66926e400ab1367b5b02af504d8c7016b9243",
        "sha256:c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    ),
}

_EXPECTED_PROFILE_MANIFEST_HASHES = {
    "authoring": "sha256:2ff1b2daa7221ab1b8f3524590b9fce6b3a1afa3ce25750d80db5e0813805980",
    "core": "sha256:110173006cf48954de2ad5c11ab697a81f5372136042197d1c15c900a0ec8237",
    "env-citylearn": "sha256:7ca8843b13ed912f3fe21ca71aec441d713bcdee2ef4530fdfd35306e583031d",
    "env-metadrive": "sha256:0883f0522e4be6a69534806095c9810f23ee74c570a3d8274934bf0f7e8158f8",
    "env-minigrid": "sha256:f18871b00fbe83a60ce88a099470bec7634ff4df0ee9fbfb2a1d21720447d14f",
    "env-mpe2": "sha256:1aab6f820ca18a26bc2c665c94aa1e4d6a72063f17dd46760e56d30b6ad64d53",
    "env-smacv2": "sha256:0f444ae81748260c321428523f7b5f5a142fb30a76310d9d1e192a35fb0257cc",
    "llm-qwen36-vllm": "sha256:0dbd067f95cf620e480f19459a26d2e1d605ae3acfbdcc57f3b6981bb1bbb456",
    "ood-openspiel": "sha256:08111743224f5a95f5bcf63f62dca1ba4939e064c7c302143a2b98494ce9515a",
    "ood-pddl": "sha256:7d2657c3efb734852346caffa2bd22840b1fb943d5e8cbd7dfe33a457c92edf7",
    _RESTRICTED_PROFILE_ID: "sha256:2e844112c8c2832801a5dea772427376b7345c4853447c69b7ba25f25de2eacc",
    "retrieval-tavily": "sha256:6e87fc2cff550546c728bebd7ad7ccff9fbd826a49035152fe397d0887238336",
    "rllib-core": "sha256:d655179755175ee164710a92dbf3b6220b80fff919a6ea67483b59b1de389428",
    "rllib-taxi-synthesis": "sha256:2c29507240d25c1a601c2fc66f424ee00ce85cc93eb131595fc291edd68f1474",
    "runner-control": "sha256:6f210691b684fd869bcb06669440cf3ba456d6497637a5ab8db69a58c5778c9a",
    "sealed-env-taxi-gold": "sha256:66dc4b6fd4fa125c3781fbbf56dbaccb739ebc454583eb861c3a6acbacb9eb2d",
    "sealed-evaluator-rllib": "sha256:afe6be91902305fab8dc63157ad4a88e2fa908d8acbd0f223ba1ff4446143d05",
}

_EXPECTED_UPSTREAM_MANIFEST_HASHES = {
    _RESTRICTED_NAME: "sha256:2bc57a316b771cb4600d9817fa21852e50b17acf15a8a3905dddd9760a721c27",
    "bytesized32": "sha256:a14ce6630f9cc41f84bc1b68b5d7719dc1644ef861b669d7c22d7bbc1ff1fb38",
    "camel-ai": "sha256:fa54bc39aeeb8a35f96a642c9f8611cc40641617f77c0582912a1f5ec621b55d",
    "camel-oasis": "sha256:f2491bd0472f7e08d9d8b4be9f347befd76be69b2a3e52961cda6624b10e8cbc",
    "citylearn": "sha256:c7040cd4c8b2971f93d898b938722182e770631c18c9ad9005569b225eb3fa65",
    "code-world-models": "sha256:471a19b6ed2decb20b4435a3db6c777ac63df5e97f29d34e0a3191afa8c80c1c",
    "gymnasium": "sha256:e426742fb4a72b82d7ad8b7d90727dea087c790b8065ecb5b7e6ff5a214e6902",
    "gymnasium-1-3": "sha256:5a592df741110b0ca33137836d36fd87a7047ec718064ba47bc0342556802c4c",
    "llamafactory": "sha256:ffd7b722e062013665d590e88de67b802f9e882f9356a88a4e7fa76d6e1e4a22",
    "metadrive": "sha256:0ce6dcf02ee2abfede446db003856c7fc00c2a7c8156175b1be1d3fc4114a52b",
    "minigrid": "sha256:d7a39745d25a6d227efddb31980d4684bd83ac8ef8bee01ca00d4b533941387a",
    "mpe2": "sha256:2a1129c030e82a582ada00af45c38e5c3e7ec9dd9fbbfaf9778bdbd0ef9f05bd",
    "open-spiel": "sha256:97a004be85c69f12d309fd2e04245a3c5d104cbf83567e512690513aa86822fd",
    "pettingzoo": "sha256:7183ed8ede776878aa04b799f5e431e6fb66bca4920a55e4fe4f8b19dd0bfeed",
    "pydantic": "sha256:ebcb196c5486050a41941ec8db3f2b7b9c5a6a1d8744bc7cfa9ddb00fe5d8ee5",
    "qwen36-35b-a3b": "sha256:3df8d4fb19302fe6c5e26862ef5313c5a371248a876e96e0dd83dc91997af682",
    "ray-rllib": "sha256:0eed49b8a29a6262f5c099e69e7432003ed25382672bf1eaa2a73ff3a552b7cb",
    "safetensors": "sha256:f211a54f889cc1cb25fed05533075c84a0e7e0f568a25273b59992475e2d4f2a",
    "sc2-assets": "sha256:24b3ce606ff3047fabb240cc46c021dc1a2cee19761e8cb650b9cbfac31c5a40",
    "scenarionet": "sha256:2dcda155d42bd5ce9e79b44b579e8ed724b788e2939ebc838d2e389149662b6a",
    "smacv2": "sha256:d24b99d73c3f3a144ab91899280e8895eb8889327611fd50e701b36bccefd1b3",
    "swanlab": "sha256:bb97b27c7fe2fac99692da80c18dcb83883ccddacc541b521e105bb90d838a1e",
    "tavily-python": "sha256:ff570312e7f2721d8db561851d47e761e359e9c7819fa9d223bbe483acc2a56b",
    "text2world": "sha256:64ff113c1fd977e7ba06dd49725156853841039a57af8d240d81286cba5d9389",
    "torch": "sha256:c1da52b0a67e08e1486f22deb6e8fdb7faeed63cd7fe36c5567b9dbef1ca9d10",
    "unified-planning": "sha256:033cf32a9d6efd9aa25d823f8c4007e49b25bef231128f928b264e3dd8164659",
    "vllm": "sha256:26d2758ca77c4c169b35c2ff34816327a9c05f50c8b8d45aafc1fae1c4785721",
}


class _ProfilePolicy(NamedTuple):
    python_version: str
    lock_hash: str
    package_versions: tuple[tuple[str, str], ...]
    repository_commits: tuple[tuple[str, str], ...]
    smoke_imports: tuple[str, ...]
    forbidden_imports: tuple[str, ...]


class _ProfileSecurityPolicy(NamedTuple):
    egress_allowlist: tuple[str, ...]
    credential_ids: tuple[str, ...]
    read_mounts: tuple[str, ...]
    write_mounts: tuple[str, ...]
    protocol_edges: tuple[str, ...]


_PROFILE_POLICIES = {
    "authoring": _ProfilePolicy(
        "3.11.13",
        "sha256:8c0024817ae171a3f7221f5a12b599a3a824bc1425a17792c456f11c1f0089ae",
        (
            ("camel-ai", "0.2.90"),
            ("httpx", "0.28.1"),
            ("lancedb", "0.36.0"),
            ("pydantic", "2.12.0"),
            ("sentence-transformers", "5.7.0"),
            ("setuptools", "84.0.0"),
        ),
        (("camel-ai", "deb286f36702ab15a2cb890c6e223a79e4ce4284"),),
        ("camel", "httpx", "lancedb", "pydantic", "sentence_transformers"),
        ("citylearn", "metadrive", "ray", "smacv2", "tavily", "vllm"),
    ),
    "core": _ProfilePolicy(
        "3.12.11",
        "sha256:69c83826aa007fce14e1a707451dea1242bf7a0adaf9f717e3538803920f75e9",
        (
            ("cryptography", "49.0.0"),
            ("jsonschema", "4.26.0"),
            ("pydantic", "2.12.0"),
            ("rfc8785", "0.1.4"),
            ("typing-extensions", "4.16.0"),
        ),
        (("pydantic", "a7928e692e5a7841c4379d1af1fd37966941dade"),),
        ("cryptography", "jsonschema", "pydantic", "rfc8785"),
        (
            "citylearn",
            "metadrive",
            "oasis",
            "open_spiel",
            "ray",
            "scenarionet",
            "smacv2",
            "tavily",
            "vllm",
        ),
    ),
    "env-citylearn": _ProfilePolicy(
        "3.11.13",
        "sha256:e7c7cc119aaad9209be2f50ab2b1ab1c7fc93746e11f0e30cc4bd540bfdec521",
        (
            ("citylearn", "2.5.0"),
            ("gymnasium", "0.28.1"),
            ("numpy", "1.26.4"),
            ("setuptools", "84.0.0"),
        ),
        (("citylearn", "29062af6d077409e1c37a3e53a6cac30fd4d02bc"),),
        ("citylearn", "gymnasium", "numpy"),
        ("metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    "env-metadrive": _ProfilePolicy(
        "3.11.13",
        "sha256:f1823b46ae8b09819d1b54358fa40085050f8177621e77f59ec5f96c6e43d208",
        (("metadrive-simulator", "0.4.3"), ("setuptools", "84.0.0")),
        (
            ("metadrive", "5bf8ea8909c4643a4099a250e6f5fb89c695d8b4"),
            ("scenarionet", "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170"),
        ),
        ("metadrive", "scenarionet"),
        ("citylearn", "ray", "smacv2", "tavily", "vllm"),
    ),
    "env-minigrid": _ProfilePolicy(
        "3.11.13",
        "sha256:c244f701592003ab1cb75a074d366192b4722b22f666b0b0e8a46ef322df03ff",
        (("gymnasium", "1.2.2"), ("minigrid", "3.1.0")),
        (("minigrid", "90928729376741a41222a257911343b97103b548"),),
        ("gymnasium", "minigrid"),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    "env-mpe2": _ProfilePolicy(
        "3.11.13",
        "sha256:59899438d7c7e447f72370fdd1063ec1209fc0697ac54bff294f003bb33b33f6",
        (("gymnasium", "1.2.2"), ("mpe2", "1.1.0"), ("pettingzoo", "1.26.1")),
        (
            ("mpe2", "7590d9d52791e321974d4fda6090fb18f34dbf49"),
            ("pettingzoo", "1756a4d7494b532651f0024ff7087ef4945432a6"),
        ),
        ("gymnasium", "mpe2", "pettingzoo"),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    "env-smacv2": _ProfilePolicy(
        "3.11.13",
        "sha256:9e68130aab2cac1ddc9be0dda081439050f84fa6b8a5cd29c7bac1229e41e6c0",
        (
            ("protobuf", "3.20.1"),
            ("pysc2", "4.0.0"),
            ("setuptools", "84.0.0"),
        ),
        (("smacv2", "577ab5a2cff2391f8df582da5731ea9cd6adf3c6"),),
        ("smacv2",),
        ("citylearn", "metadrive", "ray", "scenarionet", "swanlab", "tavily", "vllm"),
    ),
    "llm-qwen36-vllm": _ProfilePolicy(
        "3.12.11",
        "sha256:a84c36d91b87338c52a133eefbe13eddb18d31a4227390fc027362d9a1e06cca",
        (("vllm", "0.25.1+cu129"),),
        (("vllm", "752a3a504485790a2e8491cacbb35c137339ad34"),),
        (),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily"),
    ),
    "ood-openspiel": _ProfilePolicy(
        "3.11.13",
        "sha256:d539a5f5b467f95b7a0344365574d35a8b42428e12fcedd4feca88eb0ccfd4e7",
        (("open-spiel", "2.0.1"),),
        (("open-spiel", "112b77704631fc2ce7ad8e4581f6ca09798ce15a"),),
        ("open_spiel", "pyspiel"),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    "ood-pddl": _ProfilePolicy(
        "3.11.13",
        "sha256:16ae4a51eb6532782a9a50942fd9c4571f91bcf2149680202602d37fad114e8b",
        (("unified-planning", "1.3.0"),),
        (("unified-planning", "42e66926e400ab1367b5b02af504d8c7016b9243"),),
        ("unified_planning",),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    _RESTRICTED_PROFILE_ID: _ProfilePolicy(
        "3.10.18",
        "sha256:7a7e0d6336e8e0d974c03f7315276e68d9af43db8223f4eaae3ec019075eb49e",
        (),
        ((_RESTRICTED_NAME, _RESTRICTED_COMMIT.decode("ascii")),),
        (),
        (
            _RESTRICTED_NAME,
            "citylearn",
            "metadrive",
            "ray",
            "scenarionet",
            "smacv2",
            "tavily",
            "vllm",
        ),
    ),
    "retrieval-tavily": _ProfilePolicy(
        "3.11.13",
        "sha256:dcce8eb342f6d32585736a91689858cb2e499a1c3db944686e1f2189e091115d",
        (("httpx", "0.28.1"), ("tavily-python", "0.7.27")),
        (("tavily-python", "de924695765d5cf28bd1975c1cfca0cd07cd7005"),),
        ("httpx", "tavily"),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "vllm"),
    ),
    "rllib-core": _ProfilePolicy(
        "3.11.13",
        "sha256:9e62ccf2a7c768b05aae4e078f97459f51aeb04c5d2bdf5ab682ca4061d34f6a",
        (
            ("gymnasium", "1.2.2"),
            ("minigrid", "3.1.0"),
            ("mpe2", "1.1.0"),
            ("pettingzoo", "1.26.1"),
            ("ray", "2.56.1"),
            ("safetensors", "0.8.0"),
            ("torch", "2.13.0"),
        ),
        (("ray", "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a"),),
        ("gymnasium", "minigrid", "mpe2", "pettingzoo", "ray", "safetensors", "torch"),
        (
            "citylearn",
            "metadrive",
            "open_spiel",
            "scenarionet",
            "smacv2",
            "tavily",
            "vllm",
        ),
    ),
    "rllib-taxi-synthesis": _ProfilePolicy(
        "3.11.13",
        "sha256:9e62ccf2a7c768b05aae4e078f97459f51aeb04c5d2bdf5ab682ca4061d34f6a",
        (
            ("gymnasium", "1.2.2"),
            ("minigrid", "3.1.0"),
            ("mpe2", "1.1.0"),
            ("pettingzoo", "1.26.1"),
            ("ray", "2.56.1"),
            ("safetensors", "0.8.0"),
            ("torch", "2.13.0"),
        ),
        (("ray", "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a"),),
        ("gymnasium", "ray", "safetensors", "torch"),
        (
            "citylearn",
            "metadrive",
            "open_spiel",
            "scenarionet",
            "smacv2",
            "tavily",
            "vllm",
        ),
    ),
    "runner-control": _ProfilePolicy(
        "3.11.13",
        "sha256:fe88479e48ecead54b8069202654fd7a4b068b5225027571b52331610b928322",
        (("cryptography", "49.0.0"), ("pydantic", "2.12.0"), ("rfc8785", "0.1.4")),
        (),
        ("cryptography", "pydantic", "rfc8785", "ssl"),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    "sealed-env-taxi-gold": _ProfilePolicy(
        "3.11.13",
        "sha256:b9e3fd9cecd8ab841bbfbe3ce8eefd3cce4920b3f9089fdae2d36c00ca5af4a1",
        (("gymnasium", "1.3.0"),),
        (("gymnasium", "53bf3e9a884783eb72ad3fc8b15780914c97c3e1"),),
        ("gymnasium",),
        ("citylearn", "metadrive", "ray", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
    "sealed-evaluator-rllib": _ProfilePolicy(
        "3.11.13",
        "sha256:d4070ae31c74de834b223a74765aeca3874a89c727429854fbed60e2bbf6ffde",
        (
            ("cryptography", "49.0.0"),
            ("gymnasium", "1.2.2"),
            ("pettingzoo", "1.26.1"),
            ("pydantic", "2.12.0"),
            ("ray", "2.56.1"),
            ("rfc8785", "0.1.4"),
            ("safetensors", "0.8.0"),
            ("torch", "2.13.0"),
        ),
        (("ray", "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a"),),
        (
            "cryptography",
            "gymnasium",
            "pettingzoo",
            "pydantic",
            "ray",
            "rfc8785",
            "safetensors",
            "torch",
        ),
        ("citylearn", "metadrive", "scenarionet", "smacv2", "tavily", "vllm"),
    ),
}
_PROFILE_SECURITY_POLICIES = {
    "authoring": _ProfileSecurityPolicy(
        (),
        ("evidence-gateway-client.v1", "local-llm-client.v1"),
        ("/mnt/automarkov/artifacts/allowed",),
        ("/mnt/automarkov/artifacts/run",),
        ("EvidenceGateway", "LocalLlmRuntime"),
    ),
    "core": _ProfileSecurityPolicy(
        (),
        (),
        ("/mnt/automarkov/artifacts/input",),
        ("/mnt/automarkov/artifacts/output",),
        (),
    ),
    "env-citylearn": _ProfileSecurityPolicy(
        (),
        ("remote-env-server.v1",),
        ("/mnt/automarkov/assets/citylearn",),
        (),
        ("RemoteEnv",),
    ),
    "env-metadrive": _ProfileSecurityPolicy(
        (),
        ("remote-env-server.v1",),
        ("/mnt/automarkov/assets/metadrive",),
        ("/mnt/automarkov/artifacts/scenarios",),
        ("RemoteEnv",),
    ),
    "env-minigrid": _ProfileSecurityPolicy(
        (),
        ("remote-env-server.v1",),
        (),
        (),
        ("RemoteEnv",),
    ),
    "env-mpe2": _ProfileSecurityPolicy(
        (),
        ("remote-env-server.v1",),
        (),
        (),
        ("RemoteEnv",),
    ),
    "env-smacv2": _ProfileSecurityPolicy(
        (),
        ("remote-env-server.v1",),
        ("/mnt/automarkov/assets/starcraft2",),
        (),
        ("RemoteEnv",),
    ),
    "llm-qwen36-vllm": _ProfileSecurityPolicy(
        (),
        ("local-llm-server.v1",),
        ("/mnt/automarkov/models/qwen36",),
        (),
        ("LocalLlmRuntime",),
    ),
    "ood-openspiel": _ProfileSecurityPolicy(
        (),
        (),
        ("/mnt/automarkov/artifacts/input",),
        ("/mnt/automarkov/artifacts/output",),
        (),
    ),
    "ood-pddl": _ProfileSecurityPolicy(
        (),
        (),
        ("/mnt/automarkov/artifacts/input",),
        ("/mnt/automarkov/artifacts/output",),
        (),
    ),
    _RESTRICTED_PROFILE_ID: _ProfileSecurityPolicy((), (), (), (), ()),
    "retrieval-tavily": _ProfileSecurityPolicy(
        ("api.tavily.com:443",),
        ("tavily-key-lease.v1",),
        ("/mnt/automarkov/artifacts/requests",),
        ("/mnt/automarkov/artifacts/evidence",),
        ("EvidenceGateway",),
    ),
    "rllib-core": _ProfileSecurityPolicy(
        (),
        ("remote-env-client.v1",),
        ("/mnt/automarkov/artifacts/training",),
        ("/mnt/automarkov/checkpoints",),
        ("RemoteEnv",),
    ),
    "rllib-taxi-synthesis": _ProfileSecurityPolicy(
        (),
        ("remote-env-client.v1",),
        ("/mnt/automarkov/artifacts/training",),
        ("/mnt/automarkov/checkpoints",),
        ("RemoteEnv",),
    ),
    "runner-control": _ProfileSecurityPolicy(
        (),
        ("fixed-commit-signing.v1", "remote-env-issuer.v1"),
        ("/mnt/automarkov/artifacts/control",),
        ("/mnt/automarkov/artifacts/attestations",),
        ("FixedCommitRunner", "RemoteEnv"),
    ),
    "sealed-env-taxi-gold": _ProfileSecurityPolicy(
        (),
        ("remote-env-sealed-server.v1",),
        ("/mnt/automarkov/sealed/taxi",),
        (),
        ("RemoteEnv",),
    ),
    "sealed-evaluator-rllib": _ProfileSecurityPolicy(
        (),
        ("remote-env-sealed-client.v1", "sealed-evaluator-signing.v1"),
        (
            "/mnt/automarkov/artifacts/evaluation",
            "/mnt/automarkov/sealed/evaluation",
        ),
        ("/mnt/automarkov/artifacts/verdicts",),
        ("RemoteEnv", "SealedEvaluator"),
    ),
}
EXPECTED_PROFILE_IDS = frozenset(_PROFILE_POLICIES)
_EQUIVALENT_LOCK_GROUPS = (("rllib-core", "rllib-taxi-synthesis"),)


def _require_raw_object(value: object) -> object:
    if type(value) is not dict:
        raise ValueError("manifest ingress requires an exact JSON object")
    validate_and_measure_raw_json_tree(value)
    return value


def _require_sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    expected = tuple(sorted(set(values), key=lambda item: item.encode("utf-8")))
    if values != expected:
        raise ValueError(f"{field_name} must be sorted and unique")
    return values


def _require_repository_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("repository must be a valid HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _APPROVED_UPSTREAM_HOSTS
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
        or any(part in {".", ".."} for part in Path(unquote(parsed.path)).parts)
    ):
        raise ValueError("repository must use an approved immutable HTTPS URL")
    return value


def _manifest_hash(model: StrictFrozenModel) -> str:
    payload = model.model_dump(
        mode="json",
        round_trip=True,
        warnings="error",
        exclude_computed_fields=True,
    )
    if type(payload) is not dict:  # pragma: no cover - 模型固定输出 JSON object。
        raise AssertionError("manifest must serialize to a JSON object")
    return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()


class UpstreamManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.upstream-manifest.v2"]
    resource_id: ResourceId
    repository: Annotated[
        str,
        Field(strict=True, pattern=r"^https://[^\s?#]+$", max_length=2_048),
        AfterValidator(_require_repository_url),
    ]
    commit: GitCommit | None
    release: ExactVersion | None
    resolution_status: ResolutionStatus
    integration_status: IntegrationStatus
    purpose: NonEmptyText
    license: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    license_file_hash: Digest
    redistribution_policy: RedistributionPolicy
    install_mode: InstallMode
    dependency_profile: RuntimeProfileId
    data_assets: FrozenSequence[NonEmptyText]
    checksums: FrozenSequence[Digest]
    citation: NonEmptyText

    @model_validator(mode="before")
    @classmethod
    def require_raw_object(cls, value: object) -> object:
        return _require_raw_object(value)

    @model_validator(mode="after")
    def require_reconstructable_identity(self) -> Self:
        _require_sorted_unique(cast(tuple[str, ...], self.data_assets), "data_assets")
        _require_sorted_unique(cast(tuple[str, ...], self.checksums), "checksums")
        if self.resolution_status == "blocked_unresolved":
            if (
                self.commit is not None
                or self.release is not None
                or self.checksums
                or self.redistribution_policy != "prohibited"
            ):
                raise ValueError(
                    "blocked resources must preserve an unresolved identity"
                )
            if self.integration_status != "blocked_unresolved":
                raise ValueError("blocked resolution requires blocked integration")
        elif self.commit is None and self.release is None:
            raise ValueError("resolved upstreams require a release or exact commit")
        elif self.integration_status == "blocked_unresolved":
            raise ValueError("resolved upstreams cannot claim blocked integration")
        if self.resolution_status == "external_restricted" and (
            self.redistribution_policy == "permitted"
        ):
            raise ValueError(
                "external-restricted resources require a restrictive policy"
            )
        if self.resolution_status == "pinned" and (
            self.redistribution_policy != "permitted"
        ):
            raise ValueError("pinned resources must be redistributable")
        if self.integration_status == "active" and self.install_mode not in {
            "pip",
            "git_submodule",
        }:
            raise ValueError("active integrations require pip or exact Git sources")
        if (
            self.integration_status == "active"
            and self.install_mode == "pip"
            and len(self.checksums) != 1
        ):
            raise ValueError(
                "active pip integrations require one selected artifact hash"
            )
        if self.checksums and not (
            self.integration_status == "active" and self.install_mode == "pip"
        ):
            raise ValueError(
                "only active pip integrations carry selected artifact hashes"
            )
        if self.redistribution_policy != "permitted" and self.install_mode not in {
            "dataset_download",
            "external_cache",
        }:
            raise ValueError("non-permitted resources require an isolated install mode")
        if self.integration_status == "active" and self.resolution_status != "pinned":
            raise ValueError("active integrations require pinned upstreams")
        if self.integration_status == "restricted_disabled" and (
            self.resolution_status != "external_restricted"
        ):
            raise ValueError(
                "restricted-disabled integrations require restricted upstreams"
            )
        return self

    @computed_field(return_type=str)
    @property
    def manifest_hash(self) -> str:
        return _manifest_hash(self)


class RuntimeProfileManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.runtime-profile-manifest.v2"]
    profile_id: RuntimeProfileId
    python_version: Annotated[
        str,
        Field(strict=True, pattern=_PYTHON_VERSION_PATTERN),
    ]
    lockfile_path: RelativeFile
    lock_hash: Digest
    containerfile_path: RelativeFile
    build_context_files: FrozenSequence[RelativeFile]
    build_context_hash: Digest
    target_platform: PlatformId
    image_status: ImageStatus
    image_digest: Digest | None
    platform: PlatformId | None
    libc_version: NonEmptyText | None
    openssl_version: NonEmptyText | None
    ca_bundle_hash: Digest | None
    build_attestation_id: ArtifactReferenceId | None
    build_attestation_hash: Digest | None
    import_smoke_attestation_id: ArtifactReferenceId | None
    import_smoke_attestation_hash: Digest | None
    sbom_path: RelativeFile
    sbom_hash: Digest
    license_manifest_path: RelativeFile
    license_manifest_hash: Digest
    smoke_contract_path: RelativeFile
    smoke_contract_hash: Digest
    package_versions: FrozenStringMapping[ExactVersion]
    repository_commits: FrozenStringMapping[GitCommit]
    dataset_revisions: FrozenStringMapping[NonEmptyText]
    model_revisions: FrozenStringMapping[NonEmptyText]
    hardware_contract: NonEmptyText
    capabilities: FrozenSequence[NonEmptyText]
    conflict_groups: FrozenSequence[NonEmptyText]
    egress_allowlist: FrozenSequence[NonEmptyText]
    credential_ids: FrozenSequence[NonEmptyText]
    read_mounts: FrozenSequence[NonEmptyText]
    write_mounts: FrozenSequence[NonEmptyText]
    protocol_edges: FrozenSequence[NonEmptyText]
    restricted: bool = Field(strict=True)
    build_enabled: bool = Field(strict=True)
    publishable: bool = Field(strict=True)

    @model_validator(mode="before")
    @classmethod
    def require_raw_object(cls, value: object) -> object:
        return _require_raw_object(value)

    @model_validator(mode="after")
    def require_closed_profile_policy(self) -> Self:
        _require_sorted_unique(cast(tuple[str, ...], self.capabilities), "capabilities")
        _require_sorted_unique(
            cast(tuple[str, ...], self.conflict_groups),
            "conflict_groups",
        )
        for field_name in (
            "egress_allowlist",
            "credential_ids",
            "read_mounts",
            "write_mounts",
            "protocol_edges",
        ):
            _require_sorted_unique(
                cast(tuple[str, ...], getattr(self, field_name)),
                field_name,
            )
        _require_sorted_unique(
            cast(tuple[str, ...], self.build_context_files),
            "build_context_files",
        )
        if self.containerfile_path not in self.build_context_files:
            raise ValueError("build context must include the Containerfile")
        if not {".dockerignore", "pyproject.toml", "uv.lock"} <= set(
            self.build_context_files
        ):
            raise ValueError(
                "build context must include .dockerignore, pyproject.toml and uv.lock"
            )
        if self.restricted and (self.build_enabled or self.publishable):
            raise ValueError("restricted profiles must be disabled and non-publishable")
        if self.profile_id == _RESTRICTED_PROFILE_ID and not self.restricted:
            raise ValueError(f"{_RESTRICTED_NAME} profile must remain restricted")
        expected_special_status = {
            "llm-qwen36-vllm": "attached_unverified",
            _RESTRICTED_PROFILE_ID: "restricted_disabled",
        }.get(self.profile_id)
        if expected_special_status is not None:
            if self.image_status != expected_special_status:
                raise ValueError("special profile uses an invalid image state")
        elif self.image_status not in {"built", "recipe_frozen"}:
            raise ValueError("buildable profiles require a build lifecycle state")
        if self.image_status == "built":
            built_evidence = (
                self.image_digest,
                self.platform,
                self.libc_version,
                self.openssl_version,
                self.ca_bundle_hash,
                self.build_attestation_id,
                self.build_attestation_hash,
                self.import_smoke_attestation_id,
                self.import_smoke_attestation_hash,
            )
            if not self.build_enabled or any(item is None for item in built_evidence):
                raise ValueError("built profiles require complete runtime evidence")
            if self.platform != self.target_platform:
                raise ValueError("built platform must match the frozen target platform")
        elif any(
            item is not None
            for item in (
                self.image_digest,
                self.platform,
                self.libc_version,
                self.openssl_version,
                self.ca_bundle_hash,
                self.build_attestation_id,
                self.build_attestation_hash,
                self.import_smoke_attestation_id,
                self.import_smoke_attestation_hash,
            )
        ):
            raise ValueError("unbuilt profiles must not claim runtime evidence")
        if self.image_status == "recipe_frozen" and not self.build_enabled:
            raise ValueError("recipe-frozen profiles must remain buildable")
        if self.image_status == "attached_unverified" and (
            self.build_enabled or self.restricted or self.publishable
        ):
            raise ValueError(
                "attached profiles must be non-buildable, unrestricted and non-publishable"
            )
        if self.image_status == "restricted_disabled" and (
            self.build_enabled or not self.restricted
        ):
            raise ValueError(
                "restricted-disabled profiles require a restricted boundary"
            )
        return self

    @computed_field(return_type=str)
    @property
    def manifest_hash(self) -> str:
        return _manifest_hash(self)


class ProvenanceVerificationReport(StrictFrozenModel):
    schema_version: Literal["automarkov.provenance-verification-report.v1"]
    valid: bool = Field(strict=True)
    profile_count: Annotated[int, Field(strict=True, ge=0)]
    upstream_count: Annotated[int, Field(strict=True, ge=0)]
    passed_checks: FrozenSequence[NonEmptyText]
    errors: FrozenSequence[NonEmptyText]
    catalog_hash: Digest

    @model_validator(mode="before")
    @classmethod
    def require_raw_object(cls, value: object) -> object:
        return _require_raw_object(value)

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> Self:
        _require_sorted_unique(
            cast(tuple[str, ...], self.passed_checks),
            "passed_checks",
        )
        if self.valid == bool(self.errors):
            raise ValueError("verification verdict and errors disagree")
        return self


def _read_regular_file_with_stat(path: Path) -> tuple[bytes, os.stat_result]:
    if ".." in path.parts:
        raise ValueError(f"expected a normalized path without traversal: {path}")
    absolute = Path(os.path.abspath(path))
    if absolute == Path(absolute.anchor):
        raise ValueError(f"expected a regular file path: {path}")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open(absolute.anchor, directory_flags)
    try:
        for component in absolute.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(absolute.name, file_flags, dir_fd=directory_fd)
    except OSError as error:
        raise ValueError(
            f"unable to open a regular path without symlinks: {path}"
        ) from error
    finally:
        os.close(directory_fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"expected a regular file: {path}")
        if before.st_size > MAX_CANONICAL_DOCUMENT_BYTES:
            raise ValueError(f"manifest file exceeds the bounded ingress limit: {path}")
        chunks: list[bytes] = []
        remaining = MAX_CANONICAL_DOCUMENT_BYTES + 1
        while remaining:
            chunk = os.read(file_fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_CANONICAL_DOCUMENT_BYTES:
            raise ValueError(f"manifest file exceeds the bounded ingress limit: {path}")
        after = os.fstat(file_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError(f"manifest file changed during bounded read: {path}")
        return payload, before
    finally:
        os.close(file_fd)


def _read_regular_file(path: Path) -> bytes:
    return _read_regular_file_with_stat(path)[0]


def _load_json(path: Path) -> object:
    return parse_json_payload(_read_regular_file(path))


def _sorted_utf8_names(
    names: list[str],
    *,
    errors: list[str],
    kind: str,
) -> tuple[str, ...]:
    encoded: list[tuple[bytes, str]] = []
    for name in names:
        try:
            encoded.append((name.encode("utf-8"), name))
        except UnicodeEncodeError:
            errors.append(f"restricted exclusion: {kind} is not a valid UTF-8 filename")
    return tuple(name for _, name in sorted(encoded))


def _iter_distributable_files(root: Path, errors: list[str]) -> tuple[Path, ...]:
    """确定性枚举发布树；敏感与运行时目录只按路径跳过，永不读取。"""

    files: list[Path] = []
    tracked_paths: frozenset[str] | None = None
    if (root / ".git").exists():
        try:
            completed = subprocess.run(
                ("git", "ls-files", "--cached", "-z", "--"),
                cwd=root,
                check=True,
                capture_output=True,
                timeout=10.0,
            )
            tracked_paths = frozenset(
                item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
            )
            for relative_path in sorted(
                tracked_paths,
                key=lambda item: item.encode("utf-8"),
            ):
                if _is_sensitive_publish_path(
                    relative_path
                ) or _is_prohibited_tracked_publish_path(relative_path):
                    errors.append(
                        "restricted exclusion: sensitive or runtime file name is "
                        f"tracked for publication: {relative_path}"
                    )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as error:
            errors.append(
                f"restricted exclusion: cannot enumerate tracked publish tree: {error}"
            )

    def record_walk_error(error: OSError) -> None:
        errors.append(f"restricted exclusion: cannot scan publish tree: {error}")

    for directory, directory_names, filenames in os.walk(
        root,
        topdown=True,
        onerror=record_walk_error,
        followlinks=False,
    ):
        current_directory = Path(directory)
        relative_directory = current_directory.relative_to(root)
        traversable: list[str] = []
        for name in _sorted_utf8_names(
            directory_names,
            errors=errors,
            kind="directory name",
        ):
            child = current_directory / name
            relative_child = child.relative_to(root)
            relative_name = relative_child.as_posix()
            if child.is_symlink():
                errors.append(
                    "restricted exclusion: publish tree directory is a symlink: "
                    f"{relative_name}"
                )
                continue
            if relative_child.parts[:1] == ("benchmarks",) and name in {
                "gold",
                "sealed",
            }:
                continue
            if (
                name in _REPOSITORY_IGNORED_DIRECTORY_NAMES
                or relative_name in _REPOSITORY_IGNORED_ROOTS
                or relative_directory.parts[:1] == ("profiles",)
            ):
                continue
            traversable.append(name)
        directory_names[:] = traversable
        for filename in _sorted_utf8_names(
            filenames,
            errors=errors,
            kind="filename",
        ):
            path = current_directory / filename
            relative_path = path.relative_to(root)
            if relative_path == Path(".git"):
                continue
            if relative_path.parts[:1] == ("profiles",):
                continue
            sensitive_name = _is_sensitive_publish_path(relative_path.as_posix())
            if sensitive_name and (
                tracked_paths is None or relative_path.as_posix() in tracked_paths
            ):
                errors.append(
                    "restricted exclusion: sensitive or runtime file name is present in publish "
                    f"tree: {relative_path.as_posix()}"
                )
                continue
            if sensitive_name:
                continue
            relative_name = relative_path.as_posix()
            if relative_name not in _REGISTERED_DISTRIBUTABLE_FILES:
                errors.append(
                    "restricted exclusion: unregistered repository publish payload may "
                    f"contain {_RESTRICTED_NAME} ingress: {relative_name}"
                )
                continue
            files.append(path)
    return tuple(files)


def _read_exactly(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ValueError("Git object stream ended before the declared blob size")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _iter_git_index_payloads(
    root: Path,
    errors: list[str],
) -> tuple[_GitIndexPayload, ...]:
    """批量读取发布 index 的普通文件 blob；敏感路径只检查名称，不读取内容。"""

    if not (root / ".git").exists():
        return ()
    try:
        completed = subprocess.run(
            ("git", "ls-files", "--cached", "--stage", "-z", "--"),
            cwd=root,
            check=True,
            capture_output=True,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        errors.append(
            f"restricted exclusion: cannot enumerate Git index blobs: {error}"
        )
        return ()

    entries: list[tuple[str, str]] = []
    for raw_entry in completed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        try:
            metadata, raw_path = raw_entry.split(b"\t", maxsplit=1)
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            relative_path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            errors.append(
                "restricted exclusion: malformed or non-UTF-8 Git index entry"
            )
            continue
        try:
            canonical_path = _require_relative_file(relative_path)
        except ValueError:
            canonical_path = ""
        if (
            not re.fullmatch(_RELATIVE_FILE_PATTERN, relative_path)
            or canonical_path != relative_path
        ):
            errors.append(
                f"restricted exclusion: non-canonical Git index path: {relative_path!r}"
            )
            continue
        if stage != "0":
            errors.append(
                f"restricted exclusion: unresolved Git index stage for {relative_path}"
            )
            continue
        if mode not in {"100644", "100755"}:
            errors.append(
                "restricted exclusion: non-regular Git index mode for "
                f"{relative_path}: {mode}"
            )
            continue
        if _is_sensitive_publish_path(
            relative_path
        ) or _is_prohibited_tracked_publish_path(relative_path):
            continue
        entries.append((relative_path, object_id))

    if not entries:
        return ()

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ("git", "cat-file", "--batch"),
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None:
            raise ValueError("Git object reader did not expose binary pipes")
        process.stdin.write(
            b"".join(object_id.encode("ascii") + b"\n" for _, object_id in entries)
        )
        process.stdin.close()

        payloads: list[_GitIndexPayload] = []
        for relative_path, expected_object_id in entries:
            header = process.stdout.readline(MAX_CANONICAL_DOCUMENT_BYTES + 1)
            if not header.endswith(b"\n"):
                raise ValueError("Git object header is missing its terminator")
            object_id, object_type, raw_size = header[:-1].split(b" ")
            if (
                object_id.decode("ascii") != expected_object_id
                or object_type != b"blob"
            ):
                raise ValueError(
                    "Git index object identity or type differs from ls-files"
                )
            size = int(raw_size)
            if size > MAX_CANONICAL_DOCUMENT_BYTES:
                raise ValueError(
                    f"Git index blob exceeds the bounded ingress limit: {relative_path}"
                )
            payload = _read_exactly(cast(BinaryIO, process.stdout), size)
            if process.stdout.read(1) != b"\n":
                raise ValueError("Git object payload is missing its terminator")
            payloads.append(_GitIndexPayload(relative_path, payload))
        return_code = process.wait(timeout=10.0)
        if return_code != 0:
            stderr = b"" if process.stderr is None else process.stderr.read(4_096)
            raise ValueError(
                "Git object reader failed: "
                + stderr.decode("utf-8", errors="replace").strip()
            )
        return tuple(payloads)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        errors.append(f"restricted exclusion: cannot read Git index blobs: {error}")
        return ()


def _is_publishable_text(payload: bytes) -> bool:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return all(character.isprintable() or character in "\n\r\t" for character in text)


def _contains_restricted_encoded_payload(
    payload: bytes,
    markers: tuple[bytes, ...],
) -> bool:
    for candidate in _BASE64_CANDIDATE.findall(payload):
        padded = candidate + b"=" * (-len(candidate) % 4)
        try:
            decoded = b64decode(padded, altchars=b"-_", validate=True).lower()
        except Base64Error:
            continue
        if any(marker in decoded for marker in markers):
            return True
    return False


def _restricted_source_policies(
    upstreams: tuple[UpstreamManifest, ...],
) -> tuple[_RestrictedSourcePolicy, ...]:
    policies: list[_RestrictedSourcePolicy] = []
    for upstream in upstreams:
        if (
            upstream.resolution_status != "external_restricted"
            or upstream.redistribution_policy
            not in {"prohibited", "research_evaluation_only"}
        ):
            continue
        repository_path = urlsplit(upstream.repository).path.strip("/").lower()
        markers = {repository_path.encode("ascii")}
        if upstream.commit is not None:
            markers.add(upstream.commit.encode("ascii"))
        if upstream.resource_id == _RESTRICTED_NAME:
            markers.update({_RESTRICTED_PATH, _RESTRICTED_TOKEN})
        frozen_markers = tuple(sorted(markers))
        policies.append(
            _RestrictedSourcePolicy(
                resource_id=upstream.resource_id,
                markers=frozen_markers,
                encoded_markers=tuple(
                    b64encode(marker).lower() for marker in frozen_markers
                ),
            )
        )
    return tuple(
        sorted(policies, key=lambda policy: policy.resource_id.encode("ascii"))
    )


def _matching_restricted_policy(
    payload: bytes,
    policies: tuple[_RestrictedSourcePolicy, ...],
) -> _RestrictedSourcePolicy | None:
    lowered = payload.lower()
    for policy in policies:
        if (
            any(marker in lowered for marker in policy.markers)
            or any(marker in lowered for marker in policy.encoded_markers)
            or _contains_restricted_encoded_payload(payload, policy.markers)
        ):
            return policy
    return None


def _verify_publish_payload(
    *,
    relative_name: str,
    payload: bytes,
    policies: tuple[_RestrictedSourcePolicy, ...],
    errors: list[str],
    origin: str,
    allow_restricted: bool = False,
    require_nonempty: bool = False,
) -> None:
    display_name = f"{origin} {relative_name}" if origin else relative_name
    digest = "sha256:" + sha256(payload).hexdigest()
    declaration_path = relative_name in _RESTRICTED_DECLARATION_FILES
    declaration = declaration_path and (
        _RESTRICTED_DECLARATION_HASHES.get(relative_name) == digest
    )
    if declaration_path and not declaration:
        errors.append(
            "restricted exclusion: frozen declaration identity mismatch in "
            f"{display_name}"
        )
        return
    if not _is_publishable_text(payload):
        errors.append(
            f"restricted exclusion: opaque publish tree payload in {display_name}"
        )
        return
    matched_policy = _matching_restricted_policy(
        relative_name.lower().encode("utf-8") + b"\n" + payload,
        policies,
    )
    if not declaration and not allow_restricted and matched_policy is not None:
        errors.append(
            "restricted exclusion: forbidden "
            f"{matched_policy.resource_id} ingress in {display_name}"
        )
    frozen_source_hash = _REGISTERED_SOURCE_HASHES.get(relative_name)
    if frozen_source_hash is not None and frozen_source_hash != digest:
        errors.append(
            f"restricted exclusion: frozen source identity mismatch in {display_name}"
        )
    if require_nonempty and not payload:
        errors.append(f"restricted exclusion: empty operator notes in {display_name}")


def load_upstream_manifests(path: Path) -> tuple[UpstreamManifest, ...]:
    """从 canonical JSON-as-YAML catalog 加载固定 upstream 身份。"""

    raw = _load_json(path)
    if type(raw) is not list:
        raise ValueError("upstream catalog must be a JSON array")
    manifests = tuple(
        UpstreamManifest.model_validate(item, strict=True)
        for item in cast(list[object], raw)
    )
    resource_ids = tuple(manifest.resource_id for manifest in manifests)
    if len(set(resource_ids)) != len(resource_ids):
        raise ValueError("duplicate upstream resource ID")
    expected_order = tuple(sorted(resource_ids, key=lambda item: item.encode("utf-8")))
    if resource_ids != expected_order:
        raise ValueError("upstream resources must use canonical ID order")
    return manifests


def load_runtime_profiles(root: Path) -> tuple[RuntimeProfileManifest, ...]:
    """加载 profile manifests，并绑定目录名与规范顺序。"""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("profiles root must be a regular directory")
    profile_roots = tuple(
        sorted(
            (
                path
                for path in root.iterdir()
                if path.is_dir() and not path.is_symlink()
            ),
            key=lambda path: path.name.encode("utf-8"),
        )
    )
    manifests: list[RuntimeProfileManifest] = []
    for profile_root in profile_roots:
        manifest = RuntimeProfileManifest.model_validate(
            _load_json(profile_root / "profile.json"),
            strict=True,
        )
        if manifest.profile_id != profile_root.name:
            raise ValueError("profile directory and manifest identity differ")
        manifests.append(manifest)
    return tuple(manifests)


def _sha256_file(path: Path) -> str:
    return "sha256:" + sha256(_read_regular_file(path)).hexdigest()


def _build_context_hash(
    profile_root: Path,
    build_context_files: tuple[str, ...],
) -> str:
    files: list[dict[str, str]] = []
    for relative_path in build_context_files:
        content, metadata = _read_regular_file_with_stat(profile_root / relative_path)
        files.append(
            {
                "mode": "0755" if metadata.st_mode & stat.S_IXUSR else "0644",
                "path": relative_path,
                "sha256": "sha256:" + sha256(content).hexdigest(),
            }
        )
    payload = {
        "domain": "AutoMarkov-Runtime-Profile-Build-Context-v2",
        "files": files,
    }
    return "sha256:" + sha256(canonical_json_bytes(payload)).hexdigest()


def _linux_amd64_platform_tags() -> tuple[str, ...]:
    # Debian bookworm 固定 glibc 2.36；别名紧随其等价 PEP 600 tag。
    platforms: list[str] = []
    aliases = {
        17: "manylinux2014_x86_64",
        12: "manylinux2010_x86_64",
        5: "manylinux1_x86_64",
    }
    for minor in range(_GLIBC_MINOR, 4, -1):
        platforms.append(f"manylinux_2_{minor}_x86_64")
        alias = aliases.get(minor)
        if alias is not None:
            platforms.append(alias)
    platforms.append("linux_x86_64")
    return tuple(platforms)


def _target_python_tags(
    python_version: str,
    target_platform: PlatformId,
) -> tuple[Tag, ...]:
    if target_platform != "linux/amd64":  # pragma: no cover - 类型已封闭。
        raise ValueError(f"unsupported target platform: {target_platform}")
    parts = python_version.split(".")
    if len(parts) != 3:
        raise ValueError("target Python version must be major.minor.patch")
    version = (int(parts[0]), int(parts[1]))
    interpreter = f"cp{version[0]}{version[1]}"
    platforms = _linux_amd64_platform_tags()
    ordered = (
        *cpython_tags(
            version,
            abis=(f"cp{version[0]}{version[1]}",),
            platforms=platforms,
        ),
        *compatible_tags(version, interpreter=interpreter, platforms=platforms),
    )
    return tuple(dict.fromkeys(ordered))


def _target_installation_package_names(
    lock: Mapping[str, object],
    *,
    python_version: str,
    target_platform: PlatformId,
) -> frozenset[str]:
    if target_platform != "linux/amd64":  # pragma: no cover - 类型已封闭。
        raise ValueError(f"unsupported target platform: {target_platform}")
    packages = lock.get("package")
    if type(packages) is not list:
        raise ValueError("uv lock package list is missing")
    packages_by_name: dict[str, dict[str, object]] = {}
    roots: list[str] = []
    for package in packages:
        if type(package) is not dict or type(package.get("name")) is not str:
            raise ValueError("uv lock package identity is invalid")
        name = str(package["name"])
        if name in packages_by_name:
            raise ValueError(f"uv lock has ambiguous package identity: {name}")
        packages_by_name[name] = package
        source = package.get("source")
        if type(source) is dict and "virtual" in source:
            roots.append(name)
    if not roots:
        raise ValueError("uv lock has no virtual profile root")

    major, minor, _ = python_version.split(".")
    environment = {
        **default_environment(),
        "implementation_name": "cpython",
        "implementation_version": python_version,
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_python_implementation": "CPython",
        "platform_release": "unavailable",
        "platform_system": "Linux",
        "platform_version": "unavailable",
        "python_full_version": python_version,
        "python_version": f"{major}.{minor}",
        "sys_platform": "linux",
    }
    active: set[str] = set()
    enabled_extras: dict[str, set[str]] = {}
    pending: list[tuple[str, frozenset[str]]] = [(name, frozenset()) for name in roots]

    def enqueue_dependency(
        dependency: object,
        *,
        parent_name: str,
        parent_extras: frozenset[str],
    ) -> None:
        if type(dependency) is not dict or type(dependency.get("name")) is not str:
            raise ValueError(f"uv lock dependency entry is invalid: {parent_name}")
        marker = dependency.get("marker")
        if marker is not None:
            if type(marker) is not str or re.search(
                r"\b(?:platform_release|platform_version)\b",
                marker,
            ):
                raise ValueError(f"uv lock marker is not frozen: {parent_name}")
            try:
                parsed_marker = Marker(marker)
                applies = any(
                    parsed_marker.evaluate(
                        environment={**environment, "extra": extra},
                    )
                    for extra in (parent_extras or frozenset({""}))
                )
            except (InvalidMarker, UndefinedEnvironmentName) as error:
                raise ValueError(
                    f"uv lock marker is not frozen: {parent_name}"
                ) from error
            if not applies:
                return
        raw_extras = dependency.get("extra", [])
        if type(raw_extras) is str:
            child_extras = frozenset({raw_extras})
        elif type(raw_extras) is list and all(type(item) is str for item in raw_extras):
            child_extras = frozenset(cast(list[str], raw_extras))
        else:
            raise ValueError(f"uv lock dependency extras are invalid: {parent_name}")
        pending.append((str(dependency["name"]), child_extras))

    while pending:
        name, requested_extras = pending.pop()
        package = packages_by_name.get(name)
        if package is None:
            raise ValueError(f"uv lock dependency is missing: {name}")
        first_visit = name not in active
        active.add(name)
        prior_extras = enabled_extras.setdefault(name, set())
        new_extras = requested_extras - prior_extras
        prior_extras.update(new_extras)
        dependency_contexts = new_extras | ({""} if first_visit else set())
        if dependency_contexts:
            dependencies = package.get("dependencies", [])
            if type(dependencies) is not list:
                raise ValueError(f"uv lock dependencies are invalid: {name}")
            for dependency in dependencies:
                enqueue_dependency(
                    dependency,
                    parent_name=name,
                    parent_extras=frozenset(dependency_contexts),
                )
        optional_dependencies = package.get("optional-dependencies", {})
        if type(optional_dependencies) is not dict:
            raise ValueError(f"uv lock optional dependencies are invalid: {name}")
        for extra in new_extras:
            dependencies = optional_dependencies.get(extra)
            if type(dependencies) is not list:
                raise ValueError(f"uv lock extra is missing: {name}[{extra}]")
            for dependency in dependencies:
                enqueue_dependency(
                    dependency,
                    parent_name=name,
                    parent_extras=frozenset({extra}),
                )
    return frozenset(active)


def _require_approved_registry(source: object) -> str:
    if type(source) is not str or source not in _APPROVED_REGISTRY_URLS:
        host = urlsplit(source).hostname if type(source) is str else None
        raise ValueError(f"unapproved registry host: {host or source!r}")
    return source


def _require_approved_artifact_url(url: object) -> str:
    if type(url) is not str:
        raise ValueError("artifact URL must be a string")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _APPROVED_ARTIFACT_HOSTS
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"unapproved artifact host: {parsed.hostname or parsed.netloc!r}"
        )
    return url


def _require_approved_git_source(source: object) -> str:
    if type(source) is not str:
        raise ValueError("Git source must be a string")
    try:
        parsed = urlsplit(source)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Git source must be a valid HTTPS URL") from error
    revision_match = re.fullmatch(r"rev=([0-9a-f]{40})", parsed.query)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _APPROVED_GIT_HOSTS
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.strip("/")
        or any(part in {".", ".."} for part in Path(unquote(parsed.path)).parts)
        or revision_match is None
        or parsed.fragment != revision_match.group(1)
    ):
        raise ValueError("Git source must bind an approved URL to one full commit")
    return source


def _select_lock_artifact(
    package: Mapping[str, object],
    *,
    python_version: str,
    target_platform: PlatformId,
) -> tuple[str, str]:
    """为冻结的 CPython/Linux 目标选择唯一 wheel；无兼容 wheel 时选择 sdist。"""

    package_name = package.get("name")
    package_version = package.get("version")
    if type(package_name) is not str or type(package_version) is not str:
        raise ValueError("lock package identity is invalid")
    source = package.get("source")
    if type(source) is not dict or set(source) != {"registry"}:
        raise ValueError("registry artifact selection requires a registry source")
    _require_approved_registry(source["registry"])
    expected_name = canonicalize_name(package_name)
    tag_ranks = {
        tag: rank
        for rank, tag in enumerate(_target_python_tags(python_version, target_platform))
    }
    candidates: list[tuple[int, bytes, str, str]] = []
    wheels = package.get("wheels", [])
    if type(wheels) is not list:
        raise ValueError("lock package wheels must be a list")
    for artifact in wheels:
        if type(artifact) is not dict:
            raise ValueError("lock wheel must be an object")
        url = artifact.get("url")
        digest = artifact.get("hash")
        if type(url) is not str or type(digest) is not str:
            raise ValueError("lock wheel requires URL and hash")
        _require_approved_artifact_url(url)
        parsed_url = urlsplit(url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("lock wheel URL is not immutable HTTPS evidence")
        filename = unquote(Path(parsed_url.path).name)
        try:
            wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(filename)
        except InvalidWheelFilename as error:
            raise ValueError(f"invalid wheel filename: {filename}") from error
        if wheel_name != expected_name or str(wheel_version) != package_version:
            raise ValueError(f"wheel identity does not match lock: {filename}")
        matching_ranks = [tag_ranks[tag] for tag in wheel_tags if tag in tag_ranks]
        if matching_ranks:
            candidates.append((min(matching_ranks), url.encode("utf-8"), url, digest))
    if candidates:
        best_rank = min(candidate[0] for candidate in candidates)
        best_candidates = [
            candidate for candidate in candidates if candidate[0] == best_rank
        ]
        if len(best_candidates) != 1:
            raise ValueError("lock has ambiguous best compatible wheel")
        _, _, url, digest = best_candidates[0]
        return url, digest

    sdist = package.get("sdist")
    if type(sdist) is not dict:
        raise ValueError("registry package has no compatible wheel or sdist")
    url = sdist.get("url")
    digest = sdist.get("hash")
    if type(url) is not str or type(digest) is not str:
        raise ValueError("lock sdist requires URL and hash")
    _require_approved_artifact_url(url)
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("lock sdist URL is not immutable HTTPS evidence")
    filename = unquote(Path(parsed_url.path).name)
    try:
        sdist_name, sdist_version = parse_sdist_filename(filename)
    except InvalidSdistFilename as error:
        raise ValueError(f"invalid sdist filename: {filename}") from error
    if sdist_name != expected_name or str(sdist_version) != package_version:
        raise ValueError(f"sdist identity does not match lock: {filename}")
    return url, digest


def _license_evidence_comment(license_id: str, extracted_text: str) -> str:
    expected = _LICENSE_REF_EVIDENCE.get(license_id)
    if expected is None:
        raise ValueError(f"unregistered LicenseRef evidence: {license_id}")
    evidence_kind, expected_hash = expected
    actual_hash = "sha256:" + sha256(extracted_text.encode("utf-8")).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"LicenseRef evidence text mismatch: {license_id}")
    return canonical_json_bytes(
        {
            "domain": "AutoMarkov-SPDX-License-Evidence-v1",
            "evidence_kind": evidence_kind,
            "sha256": actual_hash,
        }
    ).decode("utf-8")


def _license_expression_contains(expression: str, required_license: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Za-z0-9.-]){re.escape(required_license)}(?![A-Za-z0-9.-])",
            expression,
        )
        is not None
    )


def _is_canonical_spdx_expression(expression: str) -> bool:
    try:
        return canonicalize_license_expression(expression) == expression
    except InvalidLicenseExpression:
        return False


def _expected_direct_requirements(
    profile: RuntimeProfileManifest,
    policy: _ProfilePolicy,
) -> tuple[str, ...]:
    if not profile.build_enabled:
        return ()
    requirements = {f"{name}=={version}" for name, version in policy.package_versions}
    ray_requirement = next(
        (
            requirement
            for requirement in requirements
            if requirement.startswith("ray==")
        ),
        None,
    )
    if ray_requirement is not None:
        requirements.remove(ray_requirement)
        requirements.add(ray_requirement.replace("ray==", "ray[rllib]==", 1))
    if profile.profile_id == "core":
        requirements.add("packaging==26.3")
    elif profile.profile_id == "env-metadrive":
        requirements.add(
            "scenarionet @ git+https://github.com/metadriverse/scenarionet@"
            "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170"
        )
    elif profile.profile_id == "env-smacv2":
        requirements.add(
            "smacv2 @ git+https://github.com/oxwhirl/smacv2@"
            "577ab5a2cff2391f8df582da5731ea9cd6adf3c6"
        )
    return tuple(sorted(requirements, key=lambda item: item.encode("utf-8")))


def _verify_pyproject_policy(
    profile_root: Path,
    profile: RuntimeProfileManifest,
    policy: _ProfilePolicy,
    errors: list[str],
) -> None:
    try:
        pyproject = tomllib.loads(
            _read_regular_file(profile_root / "pyproject.toml").decode("utf-8")
        )
        project = pyproject.get("project")
        tool = pyproject.get("tool")
        if type(project) is not dict or type(tool) is not dict:
            raise ValueError("project/tool tables are missing")
        dependencies = project.get("dependencies")
        if type(dependencies) is not list or any(
            type(dependency) is not str or not dependency for dependency in dependencies
        ):
            raise ValueError("project dependencies are invalid")
        actual_requirements = tuple(
            sorted(cast(list[str], dependencies), key=lambda item: item.encode("utf-8"))
        )
        if len(actual_requirements) != len(set(actual_requirements)):
            raise ValueError("project dependencies are duplicated")
        expected_python = ".".join(policy.python_version.split(".")[:2])
        next_minor = f"3.{int(expected_python.split('.')[1]) + 1}"
        if project.get("requires-python") != f">={expected_python},<{next_minor}":
            raise ValueError("project Python range does not match the frozen patch")
        uv = tool.get("uv")
        if type(uv) is not dict or uv.get("required-version") != _UV_REQUIRED_VERSION:
            raise ValueError("uv version is not frozen")
        expected_source_builds = _SOURCE_BUILD_PACKAGES.get(profile.profile_id, ())
        build_constraints = uv.get("build-constraint-dependencies", [])
        no_build_isolation = uv.get("no-build-isolation-package", [])
        if (
            type(build_constraints) is not list
            or any(type(requirement) is not str for requirement in build_constraints)
            or tuple(build_constraints)
            != (_BUILD_BACKEND_REQUIREMENTS if expected_source_builds else ())
        ):
            raise ValueError(
                "build backend constraints do not cover source packages "
                + ",".join(expected_source_builds)
            )
        if (
            type(no_build_isolation) is not list
            or any(type(package) is not str for package in no_build_isolation)
            or tuple(no_build_isolation) != expected_source_builds
        ):
            raise ValueError(
                "build isolation policy does not bind source packages "
                + ",".join(expected_source_builds)
            )
        expected_requirements = _expected_direct_requirements(profile, policy)
        if actual_requirements != expected_requirements:
            raise ValueError(
                "direct requirements differ from the central profile policy"
            )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
        errors.append(f"{profile.profile_id}: invalid pyproject policy: {error}")


def _verify_central_profile_policy(
    profile: RuntimeProfileManifest,
    policy: _ProfilePolicy,
    errors: list[str],
) -> None:
    if profile.python_version != policy.python_version:
        errors.append(
            f"{profile.profile_id}: Python {profile.python_version} differs from "
            f"central policy {policy.python_version}"
        )
    if profile.lock_hash != policy.lock_hash:
        errors.append(
            f"{profile.profile_id}: lock hash differs from central frozen policy"
        )
    expected_capabilities = _PROFILE_CAPABILITIES[profile.profile_id]
    if tuple(profile.capabilities) != expected_capabilities:
        errors.append(
            f"{profile.profile_id}: capabilities differ from central frozen policy"
        )
    security_policy = _PROFILE_SECURITY_POLICIES[profile.profile_id]
    for field_name in _ProfileSecurityPolicy._fields:
        if tuple(getattr(profile, field_name)) != getattr(
            security_policy,
            field_name,
        ):
            errors.append(
                f"{profile.profile_id}: {field_name} differs from central frozen policy"
            )
    expected_license_hash = _PROFILE_LICENSE_MANIFEST_HASHES[profile.profile_id]
    if profile.license_manifest_hash != expected_license_hash:
        errors.append(
            f"{profile.profile_id}: license manifest differs from central frozen policy"
        )
    expected_packages = dict(policy.package_versions)
    actual_packages = dict(profile.package_versions)
    for package in sorted(
        expected_packages.keys() | actual_packages.keys(),
        key=lambda item: item.encode("utf-8"),
    ):
        expected = expected_packages.get(package)
        actual = actual_packages.get(package)
        if actual != expected:
            errors.append(
                f"{profile.profile_id}: central package policy mismatch for {package}: "
                f"expected {expected!r}, got {actual!r}"
            )
    expected_repositories = dict(policy.repository_commits)
    actual_repositories = dict(profile.repository_commits)
    for repository in sorted(
        expected_repositories.keys() | actual_repositories.keys(),
        key=lambda item: item.encode("utf-8"),
    ):
        expected = expected_repositories.get(repository)
        actual = actual_repositories.get(repository)
        if actual != expected:
            errors.append(
                f"{profile.profile_id}: central repository policy mismatch for "
                f"{repository}: expected {expected!r}, got {actual!r}"
            )


def _forbidden_packages(profile_id: str) -> frozenset[str]:
    isolated = {
        package
        for package, owners in _ISOLATED_PACKAGE_OWNERS.items()
        if profile_id not in owners
    }
    return _BASE_FORBIDDEN_PACKAGES | isolated


def _containerfile_instructions(path: Path) -> tuple[str, ...]:
    try:
        text = _read_regular_file(path).decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Containerfile is not UTF-8") from error
    instructions: list[str] = []
    continued = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if re.match(r"^#\s*(?:syntax|escape|check)\s*=", line, re.IGNORECASE):
            raise ValueError("Containerfile parser directive is not frozen")
        if not line or line.startswith("#"):
            continue
        continued += line
        if continued.endswith("\\"):
            continued = continued[:-1].rstrip() + " "
            continue
        instructions.append(continued)
        continued = ""
    if continued:
        raise ValueError("Containerfile has an unterminated continuation")
    return tuple(instructions)


def _expected_build_context(profile_id: str) -> tuple[str, ...]:
    common = (".dockerignore", "Containerfile", "pyproject.toml")
    if profile_id == "rllib-taxi-synthesis":
        return (*common, "taxi_deny.py", "uv.lock")
    return (*common, "uv.lock")


def _expected_containerfile_instructions(
    profile: RuntimeProfileManifest,
) -> tuple[str, ...]:
    base = _BOOKWORM_GLIBC_BASES[profile.python_version]
    common = (
        f"FROM --platform={profile.target_platform} {base}",
        f"COPY --from={_UV_IMAGE} /uv /uvx /bin/",
    )
    source_builds = _SOURCE_BUILD_PACKAGES.get(profile.profile_id, ())
    locked_sync = "uv sync --locked --no-dev --no-install-project"
    if source_builds:
        omit_arguments = " ".join(
            f"--no-install-package {package}" for package in source_builds
        )
        sync_instruction = (
            f"RUN {locked_sync} {omit_arguments} "
            "&& test \"$(.venv/bin/python -c 'import importlib.metadata; "
            'print(importlib.metadata.version("setuptools"))\')" = "84.0.0" '
            f"&& {locked_sync}"
        )
    else:
        sync_instruction = "RUN uv sync --frozen --no-dev --no-install-project"
    if profile.profile_id == "rllib-taxi-synthesis":
        return (
            *common,
            "ENV PYTHONDONTWRITEBYTECODE=1",
            "WORKDIR /opt/automarkov-profile",
            "COPY pyproject.toml uv.lock ./",
            (
                "RUN --mount=type=bind,source=taxi_deny.py,"
                "target=/tmp/taxi_deny.py,ro UV_CACHE_DIR=/tmp/uv-cache "
                "uv sync --frozen --no-dev --no-install-project "
                "&& .venv/bin/python /tmp/taxi_deny.py harden "
                "--cache-root /tmp/uv-cache && .venv/bin/python "
                "/tmp/taxi_deny.py verify --cache-root /tmp/uv-cache"
            ),
        )
    if profile.profile_id in {"env-metadrive", "env-smacv2"}:
        return (
            *common,
            _BOOKWORM_GIT_INSTALL,
            "WORKDIR /opt/automarkov-profile",
            "COPY pyproject.toml uv.lock ./",
            sync_instruction,
        )
    return (
        *common,
        "WORKDIR /opt/automarkov-profile",
        "COPY pyproject.toml uv.lock ./",
        sync_instruction,
    )


def _verify_profile_files(
    root: Path,
    profile: RuntimeProfileManifest,
    errors: list[str],
) -> None:
    profile_root = root / profile.profile_id
    policy = _PROFILE_POLICIES.get(profile.profile_id)
    if policy is None:
        errors.append(f"{profile.profile_id}: no central profile policy")
        return
    _verify_central_profile_policy(profile, policy, errors)
    _verify_pyproject_policy(profile_root, profile, policy, errors)
    expected_context = _expected_build_context(profile.profile_id)
    if tuple(profile.build_context_files) != expected_context:
        errors.append(
            f"{profile.profile_id}: build context differs from central allowlist"
        )
    if profile.containerfile_path != "Containerfile":
        errors.append(f"{profile.profile_id}: Containerfile path is not frozen")
    try:
        instructions = _containerfile_instructions(
            profile_root / profile.containerfile_path
        )
        expected_instructions = _expected_containerfile_instructions(profile)
    except (OSError, ValueError, KeyError) as error:
        errors.append(f"{profile.profile_id}: invalid Containerfile contract: {error}")
    else:
        if instructions != expected_instructions:
            errors.append(
                f"{profile.profile_id}: Containerfile violates the frozen recipe policy"
            )
    actual_files = {
        path.name
        for path in profile_root.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    missing = sorted(_PROFILE_FILES - actual_files)
    if missing:
        errors.append(f"{profile.profile_id}: missing files {','.join(missing)}")
        return
    if profile.image_status == "built":
        errors.append(
            f"{profile.profile_id}: built evidence requires ArtifactRepository/head verification"
        )

    try:
        dockerignore_lines = tuple(
            _read_regular_file(profile_root / ".dockerignore")
            .decode("utf-8")
            .splitlines()
        )
    except (UnicodeDecodeError, ValueError) as error:
        errors.append(f"{profile.profile_id}: invalid .dockerignore: {error}")
    else:
        expected_dockerignore = (
            "*",
            *(f"!{path}" for path in profile.build_context_files),
        )
        if dockerignore_lines != expected_dockerignore:
            errors.append(
                f"{profile.profile_id}: .dockerignore does not close the build context"
            )

    try:
        actual_build_context_hash = _build_context_hash(
            profile_root,
            cast(tuple[str, ...], profile.build_context_files),
        )
    except ValueError as error:
        errors.append(f"{profile.profile_id}: {error}")
    else:
        if actual_build_context_hash != profile.build_context_hash:
            errors.append(f"{profile.profile_id}: build context hash mismatch")

    recorded_files = (
        (profile.lockfile_path, profile.lock_hash),
        (profile.sbom_path, profile.sbom_hash),
        (profile.license_manifest_path, profile.license_manifest_hash),
        (profile.smoke_contract_path, profile.smoke_contract_hash),
    )
    for relative_path, expected_hash in recorded_files:
        try:
            actual_hash = _sha256_file(profile_root / relative_path)
        except ValueError as error:
            errors.append(f"{profile.profile_id}: {error}")
            continue
        if actual_hash != expected_hash:
            errors.append(f"{profile.profile_id}: hash mismatch for {relative_path}")

    lock_packages: set[tuple[str, str]] | None = None
    locked_dependencies: set[tuple[str, str]] | None = None
    lock_evidence: (
        dict[
            tuple[str, str],
            tuple[str, str, str | None],
        ]
        | None
    ) = None
    sbom_licenses: dict[tuple[str, str], str] | None = None
    sbom_license_ref_sources: dict[str, frozenset[str]] | None = None
    try:
        lock = tomllib.loads(
            _read_regular_file(profile_root / profile.lockfile_path).decode("utf-8")
        )
        lock_items = lock.get("package")
        if type(lock_items) is not list or not lock_items:
            errors.append(f"{profile.profile_id}: invalid or empty uv lock")
        elif any(
            type(package) is not dict
            or type(package.get("name")) is not str
            or not package.get("name")
            or type(package.get("version")) is not str
            or not package.get("version")
            or type(package.get("source")) is not dict
            or (
                "sdist" in package
                and package.get("sdist") is not None
                and type(package.get("sdist")) is not dict
            )
            or type(package.get("wheels", [])) is not list
            for package in lock_items
        ):
            errors.append(f"{profile.profile_id}: invalid uv lock package schema")
        else:
            lock_evidence = {}
            try:
                target_packages = _target_installation_package_names(
                    lock,
                    python_version=profile.python_version,
                    target_platform=profile.target_platform,
                )
            except ValueError as error:
                errors.append(
                    f"{profile.profile_id}: invalid target lock closure: {error}"
                )
                target_packages = frozenset()
            source_build_packages: set[str] = set()
            lock_packages = {
                (str(package["name"]), str(package["version"]))
                for package in lock_items
            }
            forbidden = _forbidden_packages(profile.profile_id)
            for package_name in sorted(
                {canonicalize_name(str(package["name"])) for package in lock_items}
                & forbidden,
                key=lambda item: item.encode("utf-8"),
            ):
                errors.append(
                    f"{profile.profile_id}: forbidden package in lock: {package_name}"
                )
            locked_dependencies = {
                (str(package["name"]), str(package["version"]))
                for package in lock_items
                if "virtual" not in package.get("source", {})
            }
            for package in lock_items:
                source = package.get("source")
                if type(source) is not dict or len(source) != 1:
                    errors.append(
                        f"{profile.profile_id}: invalid lock source for {package.get('name')}"
                    )
                    continue
                source_key, source_value = next(iter(source.items()))
                if (
                    source_key not in {"git", "registry", "virtual"}
                    or type(source_value) is not str
                    or not source_value
                ):
                    errors.append(
                        f"{profile.profile_id}: unsupported lock source for {package.get('name')}"
                    )
                    continue
                try:
                    if source_key == "registry":
                        _require_approved_registry(source_value)
                    elif source_key == "git":
                        _require_approved_git_source(source_value)
                except ValueError as error:
                    errors.append(f"{profile.profile_id}: {error}")
                artifact_hashes: set[str] = set()
                artifacts = [package.get("sdist"), *package.get("wheels", [])]
                for artifact in artifacts:
                    if artifact is None:
                        continue
                    if type(artifact) is not dict:
                        errors.append(
                            f"{profile.profile_id}: invalid lock artifact for {package.get('name')}"
                        )
                        continue
                    try:
                        _require_approved_artifact_url(artifact.get("url"))
                    except ValueError as error:
                        errors.append(f"{profile.profile_id}: {error}")
                    artifact_hash = artifact.get("hash")
                    if (
                        type(artifact_hash) is not str
                        or not artifact_hash.startswith("sha256:")
                        or len(artifact_hash) != 71
                        or any(
                            character not in "0123456789abcdef"
                            for character in artifact_hash.removeprefix("sha256:")
                        )
                    ):
                        errors.append(
                            f"{profile.profile_id}: invalid lock artifact hash for {package.get('name')}"
                        )
                        continue
                    artifact_hashes.add(artifact_hash.removeprefix("sha256:"))
                if source_key == "registry" and not artifact_hashes:
                    errors.append(
                        f"{profile.profile_id}: registry lock has no artifact hash for {package.get('name')}"
                    )
                identity = (str(package["name"]), str(package["version"]))
                if source_key == "registry" and str(package["name"]) in target_packages:
                    try:
                        download_location, selected_hash = _select_lock_artifact(
                            package,
                            python_version=profile.python_version,
                            target_platform=profile.target_platform,
                        )
                    except ValueError as error:
                        errors.append(
                            f"{profile.profile_id}: cannot select artifact for "
                            f"{package.get('name')}: {error}"
                        )
                        continue
                    if not urlsplit(download_location).path.endswith(".whl"):
                        source_build_packages.add(str(package["name"]))
                else:
                    download_location = (
                        source_value
                        if source_key == "git"
                        and str(package["name"]) in target_packages
                        else "NOASSERTION"
                    )
                    selected_hash = None
                    if source_key == "git" and str(package["name"]) in target_packages:
                        source_build_packages.add(str(package["name"]))
                lock_evidence[identity] = (
                    canonical_json_bytes(source).decode("utf-8"),
                    download_location,
                    selected_hash,
                )
            expected_source_builds = set(
                _SOURCE_BUILD_PACKAGES.get(profile.profile_id, ())
            )
            for package_name in sorted(
                source_build_packages ^ expected_source_builds,
                key=lambda item: item.encode("utf-8"),
            ):
                errors.append(
                    f"{profile.profile_id}: build source closure differs from "
                    f"central policy for {package_name}"
                )
            for requirement in _BUILD_BACKEND_REQUIREMENTS:
                backend_name = requirement.split("==", maxsplit=1)[0]
                if expected_source_builds and backend_name not in target_packages:
                    errors.append(
                        f"{profile.profile_id}: build backend {backend_name} is not "
                        "in the hash-bound target lock/SBOM closure"
                    )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{profile.profile_id}: invalid uv lock: {error}")

    try:
        smoke = _load_json(profile_root / profile.smoke_contract_path)
        smoke_keys = {
            "schema_version",
            "profile_id",
            "enabled",
            "imports",
            "forbidden_imports",
            "failure_mode",
            "preflight",
        }
        if type(smoke) is not dict or set(smoke) != smoke_keys:
            errors.append(f"{profile.profile_id}: invalid smoke contract schema")
        elif smoke.get("schema_version") != "automarkov.profile-smoke.v1":
            errors.append(
                f"{profile.profile_id}: smoke schema version is not "
                "automarkov.profile-smoke.v1"
            )
        elif smoke.get("profile_id") != profile.profile_id:
            errors.append(f"{profile.profile_id}: invalid smoke contract identity")
        elif smoke.get("failure_mode") != "fail_closed":
            errors.append(f"{profile.profile_id}: smoke contract is not fail closed")
        elif type(smoke.get("enabled")) is not bool or any(
            type(values) is not list
            or any(type(item) is not str or not item for item in values)
            for values in (smoke.get("imports"), smoke.get("forbidden_imports"))
        ):
            errors.append(f"{profile.profile_id}: invalid smoke contract values")
        elif any(
            values != sorted(set(values), key=lambda item: item.encode("utf-8"))
            for values in (smoke["imports"], smoke["forbidden_imports"])
        ):
            errors.append(f"{profile.profile_id}: non-canonical smoke contract values")
        elif (
            tuple(smoke["imports"]) != policy.smoke_imports
            or tuple(smoke["forbidden_imports"]) != policy.forbidden_imports
        ):
            errors.append(
                f"{profile.profile_id}: smoke imports differ from central policy"
            )
        elif profile.profile_id == "rllib-taxi-synthesis" and (
            smoke["preflight"] != "taxi_deny_v1"
        ):
            errors.append(f"{profile.profile_id}: Taxi deny preflight is missing")
        elif profile.profile_id != "rllib-taxi-synthesis" and (
            smoke["preflight"] is not None
        ):
            errors.append(f"{profile.profile_id}: unexpected smoke preflight")
        elif profile.image_status in {"attached_unverified", "restricted_disabled"}:
            if smoke["enabled"] or smoke["imports"]:
                errors.append(
                    f"{profile.profile_id}: disabled smoke contract is active"
                )
        elif not smoke["enabled"] or not smoke["imports"]:
            errors.append(f"{profile.profile_id}: buildable smoke contract is disabled")
    except (ValueError, AttributeError) as error:
        errors.append(f"{profile.profile_id}: invalid smoke contract: {error}")

    try:
        sbom = _load_json(profile_root / profile.sbom_path)
        if (
            type(sbom) is not dict
            or sbom.get("spdxVersion") != "SPDX-2.3"
            or sbom.get("dataLicense") != "CC0-1.0"
            or sbom.get("name") != f"automarkov-profile-{profile.profile_id}"
            or type(sbom.get("packages")) is not list
        ):
            errors.append(f"{profile.profile_id}: invalid SPDX SBOM")
        elif lock_packages is not None:
            spdx_ids = [
                package.get("SPDXID")
                for package in sbom["packages"]
                if type(package) is dict
            ]
            if any(
                type(spdx_id) is not str or not spdx_id.startswith("SPDXRef-Package-")
                for spdx_id in spdx_ids
            ) or len(spdx_ids) != len(set(spdx_ids)):
                errors.append(
                    f"{profile.profile_id}: invalid or duplicate SPDX package ID"
                )
            relationships = sbom.get("relationships")
            if type(relationships) is not list or any(
                type(relationship) is not dict
                or set(relationship)
                != {"relatedSpdxElement", "relationshipType", "spdxElementId"}
                or type(relationship.get("relatedSpdxElement")) is not str
                or type(relationship.get("relationshipType")) is not str
                or type(relationship.get("spdxElementId")) is not str
                for relationship in relationships
            ):
                errors.append(f"{profile.profile_id}: invalid SPDX relationships")
            else:
                package_ids = {
                    str(package.get("name")): str(package.get("SPDXID"))
                    for package in sbom["packages"]
                    if type(package) is dict
                }
                expected_descriptions = {
                    (
                        "SPDXRef-DOCUMENT",
                        "DESCRIBES",
                        package_id,
                    )
                    for package_id in package_ids.values()
                }
                actual_descriptions = {
                    (
                        str(relationship["spdxElementId"]),
                        str(relationship["relationshipType"]),
                        str(relationship["relatedSpdxElement"]),
                    )
                    for relationship in relationships
                    if relationship["relationshipType"] == "DESCRIBES"
                }
                source_builds = _SOURCE_BUILD_PACKAGES.get(profile.profile_id, ())
                backend_id = package_ids.get("setuptools")
                expected_build_relationships = (
                    {
                        (backend_id, "BUILD_DEPENDENCY_OF", package_ids.get(package))
                        for package in source_builds
                    }
                    if backend_id is not None
                    else set()
                )
                actual_build_relationships = {
                    (
                        str(relationship["spdxElementId"]),
                        str(relationship["relationshipType"]),
                        str(relationship["relatedSpdxElement"]),
                    )
                    for relationship in relationships
                    if relationship["relationshipType"] == "BUILD_DEPENDENCY_OF"
                }
                if actual_descriptions != expected_descriptions:
                    errors.append(
                        f"{profile.profile_id}: SPDX DESCRIBES relationships do not "
                        "cover the exact lock"
                    )
                if (
                    None
                    in {
                        item
                        for relation in expected_build_relationships
                        for item in relation
                    }
                    or actual_build_relationships != expected_build_relationships
                ):
                    errors.append(
                        f"{profile.profile_id}: SPDX build dependencies do not match "
                        "the frozen source-build closure"
                    )
                if any(
                    relationship["relationshipType"]
                    not in {"BUILD_DEPENDENCY_OF", "DESCRIBES"}
                    for relationship in relationships
                ):
                    errors.append(
                        f"{profile.profile_id}: unsupported SPDX relationship"
                    )
            if any(
                type(package) is not dict
                or package.get("filesAnalyzed") is not False
                or package.get("licenseConcluded") != package.get("licenseDeclared")
                for package in sbom["packages"]
            ):
                errors.append(f"{profile.profile_id}: invalid SPDX package policy")
            sbom_entries = [
                (
                    str(package.get("name")),
                    str(package.get("versionInfo")),
                    str(package.get("licenseDeclared", "")),
                )
                for package in sbom["packages"]
                if type(package) is dict
            ]
            if any(
                not _is_canonical_spdx_expression(license_name)
                for _, _, license_name in sbom_entries
            ):
                errors.append(f"{profile.profile_id}: invalid SPDX license expression")
            sbom_packages = {(name, version) for name, version, _ in sbom_entries}
            if sbom_packages != lock_packages:
                errors.append(f"{profile.profile_id}: SBOM does not cover exact lock")
            elif len(sbom_entries) != len(sbom_packages):
                errors.append(f"{profile.profile_id}: duplicate SBOM package")
            elif [entry[:2] for entry in sbom_entries] != sorted(
                sbom_packages,
                key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")),
            ):
                errors.append(f"{profile.profile_id}: non-canonical SBOM package order")
            else:
                sbom_licenses = {
                    (name, version): license_name
                    for name, version, license_name in sbom_entries
                }
                required_license_refs = {
                    license_ref
                    for _, _, license_name in sbom_entries
                    for license_ref in _LICENSE_REF_PATTERN.findall(license_name)
                }
                extracted_licenses = sbom.get("hasExtractedLicensingInfos", [])
                if type(extracted_licenses) is not list or any(
                    type(license_info) is not dict
                    or set(license_info)
                    != {"comment", "extractedText", "licenseId", "name", "seeAlsos"}
                    or type(license_info.get("comment")) is not str
                    or type(license_info.get("extractedText")) is not str
                    or not license_info.get("extractedText")
                    or type(license_info.get("licenseId")) is not str
                    or type(license_info.get("name")) is not str
                    or not license_info.get("name")
                    or type(license_info.get("seeAlsos")) is not list
                    or not license_info.get("seeAlsos")
                    or any(
                        type(source) is not str or not source.startswith("https://")
                        for source in license_info.get("seeAlsos", [])
                    )
                    or license_info.get("seeAlsos")
                    != sorted(set(license_info.get("seeAlsos", [])))
                    for license_info in extracted_licenses
                ):
                    errors.append(
                        f"{profile.profile_id}: invalid SPDX extracted license info"
                    )
                else:
                    invalid_evidence = False
                    for license_info in extracted_licenses:
                        try:
                            expected_comment = _license_evidence_comment(
                                str(license_info["licenseId"]),
                                str(license_info["extractedText"]),
                            )
                        except ValueError:
                            invalid_evidence = True
                            break
                        if license_info["comment"] != expected_comment:
                            invalid_evidence = True
                            break
                    if invalid_evidence:
                        errors.append(
                            f"{profile.profile_id}: SPDX LicenseRef evidence is not frozen"
                        )
                    extracted_ids = [
                        str(license_info["licenseId"])
                        for license_info in extracted_licenses
                    ]
                    if extracted_ids != sorted(set(extracted_ids)):
                        errors.append(
                            f"{profile.profile_id}: non-canonical SPDX extracted licenses"
                        )
                    elif set(extracted_ids) != required_license_refs:
                        errors.append(
                            f"{profile.profile_id}: SPDX LicenseRef definitions do not match packages"
                        )
                    else:
                        sbom_license_ref_sources = {
                            str(license_info["licenseId"]): frozenset(
                                str(source) for source in license_info["seeAlsos"]
                            )
                            for license_info in extracted_licenses
                        }
                if lock_evidence is not None:
                    for package in sbom["packages"]:
                        if type(package) is not dict:
                            errors.append(
                                f"{profile.profile_id}: invalid SPDX package entry"
                            )
                            continue
                        identity = (
                            str(package.get("name", "")),
                            str(package.get("versionInfo", "")),
                        )
                        expected_evidence = lock_evidence.get(identity)
                        if expected_evidence is None:
                            continue
                        source_info, download_location, selected_hash = (
                            expected_evidence
                        )
                        if (
                            package.get("sourceInfo") != source_info
                            or package.get("downloadLocation") != download_location
                        ):
                            errors.append(
                                f"{profile.profile_id}: SBOM source does not match lock for {identity[0]}"
                            )
                        checksums = package.get("checksums", [])
                        if type(checksums) is not list or any(
                            type(checksum) is not dict
                            or set(checksum) != {"algorithm", "checksumValue"}
                            or checksum.get("algorithm") != "SHA256"
                            or type(checksum.get("checksumValue")) is not str
                            or len(cast(str, checksum.get("checksumValue"))) != 64
                            or any(
                                character not in "0123456789abcdef"
                                for character in cast(
                                    str,
                                    checksum.get("checksumValue"),
                                )
                            )
                            for checksum in checksums
                        ):
                            errors.append(
                                f"{profile.profile_id}: invalid SPDX checksum for {identity[0]}"
                            )
                            continue
                        recorded_hashes = [
                            str(checksum["checksumValue"]) for checksum in checksums
                        ]
                        if recorded_hashes != sorted(set(recorded_hashes)):
                            errors.append(
                                f"{profile.profile_id}: non-canonical SPDX checksums for {identity[0]}"
                            )
                        expected_hashes = (
                            []
                            if selected_hash is None
                            else [selected_hash.removeprefix("sha256:")]
                        )
                        if recorded_hashes != expected_hashes:
                            errors.append(
                                f"{profile.profile_id}: SBOM checksum does not match lock for {identity[0]}"
                            )
    except (ValueError, AttributeError) as error:
        errors.append(f"{profile.profile_id}: invalid SPDX SBOM: {error}")

    try:
        licenses = _load_json(profile_root / profile.license_manifest_path)
        if (
            type(licenses) is not dict
            or set(licenses) != {"schema_version", "profile_id", "dependencies"}
            or licenses.get("schema_version") != "automarkov.profile-licenses.v1"
            or licenses.get("profile_id") != profile.profile_id
            or type(licenses.get("dependencies")) is not list
        ):
            errors.append(f"{profile.profile_id}: invalid license manifest")
        else:
            dependency_entries = licenses["dependencies"]
            if any(
                type(dependency) is not dict
                or set(dependency) != {"license", "name", "source", "version"}
                or any(
                    type(dependency[field]) is not str or not dependency[field]
                    for field in ("license", "name", "source", "version")
                )
                for dependency in dependency_entries
            ):
                errors.append(f"{profile.profile_id}: invalid license entry")
                dependency_entries = []
            licensed_dependencies = {
                (str(dependency.get("name", "")), str(dependency.get("version", "")))
                for dependency in dependency_entries
                if type(dependency) is dict
            }
            license_values = {
                str(dependency.get("license", "")).strip().upper()
                for dependency in dependency_entries
                if type(dependency) is dict
            }
            if any(
                not _is_canonical_spdx_expression(str(dependency.get("license", "")))
                for dependency in dependency_entries
                if type(dependency) is dict
            ):
                errors.append(f"{profile.profile_id}: invalid SPDX license expression")
            expected_order = sorted(
                licensed_dependencies,
                key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")),
            )
            actual_order = [
                (str(dependency["name"]), str(dependency["version"]))
                for dependency in dependency_entries
            ]
            if actual_order != expected_order:
                errors.append(
                    f"{profile.profile_id}: license entries are not canonical"
                )
            if (
                locked_dependencies is None
                or licensed_dependencies != locked_dependencies
            ):
                errors.append(
                    f"{profile.profile_id}: license manifest does not cover exact lock"
                )
            if license_values & {"", "NONE", "NOASSERTION", "UNKNOWN"}:
                errors.append(f"{profile.profile_id}: unknown dependency license")
            if any(
                not str(dependency["source"]).startswith("https://")
                or any(
                    moving_ref in str(dependency["source"]).lower()
                    for moving_ref in ("/head/", "/latest/", "/main/", "/master/")
                )
                for dependency in dependency_entries
            ):
                errors.append(
                    f"{profile.profile_id}: dependency license source is not immutable HTTPS evidence"
                )
            if sbom_licenses is not None and any(
                sbom_licenses.get((str(dependency["name"]), str(dependency["version"])))
                != dependency["license"]
                for dependency in dependency_entries
            ):
                errors.append(f"{profile.profile_id}: SBOM license mismatch")
            expected_license_ref_sources: dict[str, set[str]] = {}
            for dependency in dependency_entries:
                for license_ref in _LICENSE_REF_PATTERN.findall(
                    str(dependency["license"])
                ):
                    expected_license_ref_sources.setdefault(license_ref, set()).add(
                        str(dependency["source"])
                    )
            if sbom_license_ref_sources != {
                license_ref: frozenset(sources)
                for license_ref, sources in expected_license_ref_sources.items()
            }:
                errors.append(
                    f"{profile.profile_id}: SPDX LicenseRef sources do not match license evidence"
                )
    except (ValueError, AttributeError) as error:
        errors.append(f"{profile.profile_id}: invalid license manifest: {error}")


def _verify_isolation(
    root: Path,
    profiles: Mapping[str, RuntimeProfileManifest],
    errors: list[str],
) -> None:
    exclusive_packages = {
        "citylearn": "env-citylearn",
        "scenarionet": "env-metadrive",
        "smacv2": "env-smacv2",
    }
    lock_owners_by_package = {package: set() for package in exclusive_packages}
    for profile in profiles.values():
        try:
            lock = tomllib.loads(
                _read_regular_file(
                    root / profile.profile_id / profile.lockfile_path
                ).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
            continue
        packages = lock.get("package")
        if type(packages) is not list:
            continue
        names = {
            str(item.get("name", "")).lower().replace("_", "-")
            for item in packages
            if type(item) is dict
        }
        for package in exclusive_packages.keys() & names:
            lock_owners_by_package[package].add(profile.profile_id)

    for package, expected_owner in exclusive_packages.items():
        owners = {
            profile.profile_id
            for profile in profiles.values()
            if package in profile.package_versions
            or package in profile.repository_commits
        }
        if owners != {expected_owner}:
            errors.append(f"{package}: invalid profile owners {sorted(owners)}")
        lock_owners = lock_owners_by_package[package]
        if lock_owners != {expected_owner}:
            errors.append(f"{package}: invalid lock owners {sorted(lock_owners)}")

    restricted = profiles.get(_RESTRICTED_PROFILE_ID)
    if restricted is None or not restricted.restricted:
        errors.append(f"{_RESTRICTED_NAME} restricted profile is missing")
    elif restricted.build_enabled or restricted.publishable:
        errors.append(f"{_RESTRICTED_NAME} restricted profile is enabled")


def _verify_equivalent_locks(
    profiles: Mapping[str, RuntimeProfileManifest],
    errors: list[str],
) -> None:
    for group in _EQUIVALENT_LOCK_GROUPS:
        lock_hashes = {
            profiles[profile_id].lock_hash
            for profile_id in group
            if profile_id in profiles
        }
        if len(lock_hashes) != 1:
            errors.append(
                "equivalent profiles do not share one frozen lock: " + ",".join(group)
            )


def _verify_semantic_bindings(
    root: Path,
    profiles: Mapping[str, RuntimeProfileManifest],
    upstreams: tuple[UpstreamManifest, ...],
    errors: list[str],
) -> None:
    upstream_by_id = {upstream.resource_id: upstream for upstream in upstreams}
    active_ids = {
        upstream.resource_id
        for upstream in upstreams
        if upstream.integration_status == "active"
    }
    if active_ids != set(_ACTIVE_UPSTREAM_IDENTITIES):
        errors.append("active upstream catalog differs from the central frozen BOM")
    for upstream in upstreams:
        if upstream.dependency_profile not in profiles:
            errors.append(
                f"{upstream.resource_id}: unknown dependency profile {upstream.dependency_profile}"
            )
        if upstream.integration_status == "active":
            expected_identity = _ACTIVE_UPSTREAM_IDENTITIES.get(upstream.resource_id)
            actual_identity = (
                upstream.repository,
                upstream.commit,
                upstream.license_file_hash,
            )
            if expected_identity != actual_identity:
                errors.append(
                    f"{upstream.resource_id}: active upstream source identity differs "
                    "from the central frozen BOM"
                )
            repository = urlsplit(upstream.repository)
            if repository.hostname not in _APPROVED_GIT_HOSTS:
                errors.append(
                    f"{upstream.resource_id}: unapproved upstream host: "
                    f"{repository.hostname or repository.netloc!r}"
                )
    locks_by_profile: dict[str, dict[str, object]] = {}
    licenses_by_profile: dict[str, dict[str, str]] = {}
    profile_repository_aliases = {"ray": "ray-rllib"}
    for profile in profiles.values():
        try:
            lock = tomllib.loads(
                _read_regular_file(
                    root / "profiles" / profile.profile_id / profile.lockfile_path
                ).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
            continue
        packages = lock.get("package")
        if type(packages) is not list:
            continue
        lock_versions = {
            str(item.get("name", "")).lower().replace("_", "-"): str(
                item.get("version", "")
            )
            for item in packages
            if type(item) is dict
        }
        locks_by_profile[profile.profile_id] = {
            str(item.get("name", "")).lower().replace("_", "-"): item
            for item in packages
            if type(item) is dict
        }
        try:
            license_manifest = _load_json(
                root / "profiles" / profile.profile_id / profile.license_manifest_path
            )
        except (ValueError, AttributeError):
            license_manifest = None
        if (
            type(license_manifest) is dict
            and type(license_manifest.get("dependencies")) is list
        ):
            licenses_by_profile[profile.profile_id] = {
                str(item["name"]): str(item["license"])
                for item in license_manifest["dependencies"]
                if type(item) is dict
                and type(item.get("name")) is str
                and type(item.get("license")) is str
            }
        for package, expected_version in profile.package_versions.items():
            normalized = package.lower().replace("_", "-")
            locked_version = lock_versions.get(normalized)
            if locked_version != expected_version and (
                profile.build_enabled or locked_version is not None
            ):
                errors.append(
                    f"{profile.profile_id}: package summary does not match lock for {package}"
                )

        for repository, expected_commit in profile.repository_commits.items():
            resource_id = profile_repository_aliases.get(repository, repository)
            if repository == "gymnasium" and lock_versions.get("gymnasium") == "1.3.0":
                resource_id = "gymnasium-1-3"
            upstream = upstream_by_id.get(resource_id)
            if upstream is None or upstream.commit != expected_commit:
                errors.append(
                    f"{profile.profile_id}: repository summary does not match upstream for {repository}"
                )

        qwen_revision = profile.model_revisions.get("Qwen/Qwen3.6-35B-A3B")
        if qwen_revision is not None:
            qwen = upstream_by_id.get("qwen36-35b-a3b")
            if qwen is None or qwen.commit != qwen_revision:
                errors.append(
                    f"{profile.profile_id}: model revision does not match Qwen upstream"
                )

    upstream_package_aliases = {
        "gymnasium-1-3": "gymnasium",
        "metadrive": "metadrive-simulator",
        "ray-rllib": "ray",
    }
    for upstream in upstreams:
        if upstream.integration_status != "active":
            continue
        packages = locks_by_profile.get(upstream.dependency_profile, {})
        package_name = upstream_package_aliases.get(
            upstream.resource_id,
            upstream.resource_id,
        )
        package = packages.get(package_name)
        if type(package) is not dict:
            errors.append(
                f"{upstream.resource_id}: active upstream is absent from its dependency profile"
            )
            continue
        package_license = licenses_by_profile.get(
            upstream.dependency_profile,
            {},
        ).get(package_name)
        if package_license is None or not _license_expression_contains(
            package_license,
            upstream.license,
        ):
            errors.append(
                f"{upstream.resource_id}: upstream license does not match dependency license evidence"
            )
        if upstream.release is not None and package.get("version") != upstream.release:
            errors.append(
                f"{upstream.resource_id}: upstream release does not match dependency lock"
            )
        if upstream.install_mode == "pip":
            profile = profiles[upstream.dependency_profile]
            try:
                _, selected_hash = _select_lock_artifact(
                    package,
                    python_version=profile.python_version,
                    target_platform=profile.target_platform,
                )
            except ValueError as error:
                errors.append(
                    f"{upstream.resource_id}: selected artifact is invalid: {error}"
                )
                continue
            if tuple(upstream.checksums) != (selected_hash,):
                errors.append(
                    f"{upstream.resource_id}: upstream checksum does not match selected artifact"
                )
        elif upstream.install_mode == "git_submodule":
            source = package.get("source")
            expected_git = (
                f"{upstream.repository}?rev={upstream.commit}#{upstream.commit}"
            )
            if type(source) is not dict or source.get("git") != expected_git:
                errors.append(
                    f"{upstream.resource_id}: upstream commit does not match Git lock source"
                )


def _verify_restricted_exclusion(
    root: Path,
    profiles: Mapping[str, RuntimeProfileManifest],
    upstreams: tuple[UpstreamManifest, ...],
    errors: list[str],
) -> None:
    policies = _restricted_source_policies(upstreams)
    candidates: list[Path] = []
    allowed_profile_notes: set[Path] = set()
    profiles_root = root / "profiles"
    profile_paths: list[Path] = []

    def record_walk_error(error: OSError) -> None:
        errors.append(
            f"restricted exclusion: cannot scan profiles publish tree: {error}"
        )

    for directory, directory_names, filenames in os.walk(
        profiles_root,
        topdown=True,
        onerror=record_walk_error,
        followlinks=False,
    ):
        current_directory = Path(directory)
        traversable_directories: list[str] = []
        for name in _sorted_utf8_names(
            directory_names,
            errors=errors,
            kind="profile directory name",
        ):
            child = current_directory / name
            relative_child = child.relative_to(profiles_root)
            if child.is_symlink():
                errors.append(
                    "restricted exclusion: profile publish tree directory is a "
                    f"symlink: profiles/{relative_child}"
                )
                continue
            if name in _PUBLISH_TREE_IGNORED_DIRECTORIES:
                continue
            profile = profiles.get(relative_child.parts[0])
            registered_restricted_root = (
                profile is not None
                and profile.restricted
                and len(relative_child.parts) == 1
            )
            lowered_path = relative_child.as_posix().lower().encode("utf-8")
            matched_policy = _matching_restricted_policy(lowered_path, policies)
            if not registered_restricted_root and matched_policy is not None:
                errors.append(
                    "restricted exclusion: forbidden "
                    f"{matched_policy.resource_id} ingress in "
                    f"profiles/{relative_child}"
                )
                continue
            traversable_directories.append(name)
        directory_names[:] = traversable_directories
        profile_paths.extend(
            current_directory / filename
            for filename in _sorted_utf8_names(
                filenames,
                errors=errors,
                kind="profile filename",
            )
        )
    for path in profile_paths:
        relative_profile_path = path.relative_to(profiles_root)
        profile = profiles.get(relative_profile_path.parts[0])
        registered_profile_file = (
            profile is not None
            and len(relative_profile_path.parts) == 2
            and relative_profile_path.parts[1]
            in (_PROFILE_FILES | set(profile.build_context_files))
        )
        if not registered_profile_file and (
            relative_profile_path.name not in _ALLOWED_UNREGISTERED_PROFILE_TEXT_FILES
            or len(relative_profile_path.parts) != 2
        ):
            errors.append(
                "restricted exclusion: unregistered profile payload in "
                f"profiles/{relative_profile_path}"
            )
            continue
        if not (registered_profile_file and profile is not None and profile.restricted):
            candidates.append(path)
            if not registered_profile_file:
                allowed_profile_notes.add(path)
    candidates.extend(_iter_distributable_files(root, errors))

    for path in candidates:
        relative_path = path.relative_to(root)
        relative_name = relative_path.as_posix()
        try:
            payload = _read_regular_file(path)
        except (OSError, ValueError) as error:
            errors.append(f"restricted exclusion: {error}")
            continue
        _verify_publish_payload(
            relative_name=relative_name,
            payload=payload,
            policies=policies,
            errors=errors,
            origin="",
            require_nonempty=path in allowed_profile_notes,
        )

    for indexed in _iter_git_index_payloads(root, errors):
        relative_name = indexed.relative_path
        parts = Path(relative_name).parts
        allow_restricted = False
        require_nonempty = False
        registered = relative_name in (
            _REGISTERED_DISTRIBUTABLE_FILES | _TRACKED_IGNORED_ROOT_SENTINELS
        )
        if parts[:1] == ("profiles",) and len(parts) == 3:
            profile = profiles.get(parts[1])
            registered_profile_file = profile is not None and parts[2] in (
                _PROFILE_FILES | set(profile.build_context_files)
            )
            operator_notes = parts[2] in _ALLOWED_UNREGISTERED_PROFILE_TEXT_FILES
            registered = registered_profile_file or operator_notes
            allow_restricted = bool(
                registered_profile_file and profile is not None and profile.restricted
            )
            require_nonempty = operator_notes
        if not registered:
            errors.append(
                "restricted exclusion: unregistered Git index publish payload may "
                f"contain {_RESTRICTED_NAME} ingress: {relative_name}"
            )
        _verify_publish_payload(
            relative_name=relative_name,
            payload=indexed.payload,
            policies=policies,
            errors=errors,
            origin="Git index",
            allow_restricted=allow_restricted,
            require_nonempty=require_nonempty,
        )


def verify_provenance(repository_root: Path) -> ProvenanceVerificationReport:
    """验证仓库 provenance 元数据；该报告不宣称 OCI image runtime-ready。"""

    root = repository_root.resolve(strict=True)
    errors: list[str] = []
    try:
        upstreams = load_upstream_manifests(root / "references" / "manifest.yaml")
    except (OSError, ValueError) as error:
        upstreams = ()
        errors.append(f"upstream catalog: {error}")
    try:
        loaded_profiles = load_runtime_profiles(root / "profiles")
    except (OSError, ValueError) as error:
        loaded_profiles = ()
        errors.append(f"profile catalog: {error}")

    profiles = {profile.profile_id: profile for profile in loaded_profiles}
    if {upstream.resource_id for upstream in upstreams} != set(
        _EXPECTED_UPSTREAM_MANIFEST_HASHES
    ):
        errors.append("upstream catalog does not contain the exact registered set")
    if set(profiles) != EXPECTED_PROFILE_IDS:
        errors.append(
            "runtime profile catalog does not contain the exact registered set"
        )
    for upstream in upstreams:
        if (
            _EXPECTED_UPSTREAM_MANIFEST_HASHES.get(upstream.resource_id)
            != upstream.manifest_hash
        ):
            errors.append(
                f"{upstream.resource_id}: central frozen upstream identity mismatch"
            )
    for profile in loaded_profiles:
        if (
            _EXPECTED_PROFILE_MANIFEST_HASHES.get(profile.profile_id)
            != profile.manifest_hash
        ):
            errors.append(
                f"{profile.profile_id}: central frozen profile identity mismatch"
            )
        try:
            _verify_profile_files(root / "profiles", profile, errors)
        except (OSError, ValueError) as error:
            errors.append(f"{profile.profile_id}: {error}")
    _verify_isolation(root / "profiles", profiles, errors)
    _verify_equivalent_locks(profiles, errors)
    _verify_semantic_bindings(root, profiles, upstreams, errors)
    _verify_restricted_exclusion(root, profiles, upstreams, errors)

    catalog_payload = {
        "domain": "AutoMarkov-Provenance-Catalog-v1",
        "profiles": [profile.manifest_hash for profile in loaded_profiles],
        "upstreams": [upstream.manifest_hash for upstream in upstreams],
    }
    catalog_hash = "sha256:" + sha256(canonical_json_bytes(catalog_payload)).hexdigest()
    passed_checks = (
        ()
        if errors
        else (
            "build_context_hashes",
            "image_states",
            "license_manifests",
            "lock_hashes",
            "manifest_hashes",
            "profile_isolation",
            "restricted_exclusion",
            "sboms",
            "smoke_contracts",
        )
    )
    return ProvenanceVerificationReport.model_validate(
        {
            "schema_version": "automarkov.provenance-verification-report.v1",
            "valid": not errors,
            "profile_count": len(loaded_profiles),
            "upstream_count": len(upstreams),
            "passed_checks": list(passed_checks),
            "errors": sorted(set(errors), key=lambda item: item.encode("utf-8")),
            "catalog_hash": catalog_hash,
        },
        strict=True,
    )


__all__ = [
    "EXPECTED_PROFILE_IDS",
    "ProvenanceVerificationReport",
    "RuntimeProfileManifest",
    "UpstreamManifest",
    "load_runtime_profiles",
    "load_upstream_manifests",
    "verify_provenance",
]
