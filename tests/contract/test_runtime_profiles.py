from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from pydantic import ValidationError

from automarkov.domain.canonical import canonical_json_bytes

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PROFILES_ROOT = _REPOSITORY_ROOT / "profiles"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_PROFILE_IDS = {
    "authoring",
    "core",
    "env-citylearn",
    "env-metadrive",
    "env-minigrid",
    "env-mpe2",
    "env-smacv2",
    "llm-qwen36-vllm",
    "ood-openspiel",
    "ood-pddl",
    "replication-agent2world-restricted",
    "retrieval-tavily",
    "rllib-core",
    "rllib-taxi-synthesis",
    "runner-control",
    "sealed-env-taxi-gold",
    "sealed-evaluator-rllib",
}
_PROFILE_FILES = {
    ".dockerignore",
    "Containerfile",
    "license-manifest.json",
    "profile.json",
    "pyproject.toml",
    "sbom.spdx.json",
    "smoke.json",
    "uv.lock",
}
_CONFLICT_PACKAGE_OWNERS = {
    "citylearn": "env-citylearn",
    "scenarionet": "env-metadrive",
    "smacv2": "env-smacv2",
}
_EXPECTED_REPOSITORY_COMMITS = {
    "env-citylearn": {"citylearn": "29062af6d077409e1c37a3e53a6cac30fd4d02bc"},
    "env-metadrive": {
        "metadrive": "5bf8ea8909c4643a4099a250e6f5fb89c695d8b4",
        "scenarionet": "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170",
    },
    "env-smacv2": {"smacv2": "577ab5a2cff2391f8df582da5731ea9cd6adf3c6"},
}
_RUNTIME_SECURITY_IDENTITY_VALUES = {
    "credential_ids": ("credential_alpha", "credential_beta"),
    "egress_allowlist": (
        "api.example.invalid:443",
        "registry.example.invalid:443",
    ),
    "protocol_edges": ("local_llm.inference.v1", "remote_env.step.v1"),
    "read_mounts": ("/inputs/alpha", "/inputs/beta"),
    "write_mounts": ("/outputs/alpha", "/outputs/beta"),
}


def _provenance() -> ModuleType:
    try:
        return importlib.import_module("automarkov.security.provenance")
    except ModuleNotFoundError:
        pytest.fail("T04 requires the public automarkov.provenance deep module")


def _profile_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.runtime-profile-manifest.v2",
        "profile_id": "env-citylearn",
        "python_version": "3.12.11",
        "lockfile_path": "uv.lock",
        "lock_hash": "sha256:" + "1" * 64,
        "containerfile_path": "Containerfile",
        "build_context_files": [
            ".dockerignore",
            "Containerfile",
            "pyproject.toml",
            "uv.lock",
        ],
        "build_context_hash": "sha256:" + "2" * 64,
        "target_platform": "linux/amd64",
        "image_status": "built",
        "image_digest": "sha256:" + "2" * 64,
        "platform": "linux/amd64",
        "libc_version": "glibc-2.36",
        "openssl_version": "OpenSSL 3.0.17 1 Jul 2025",
        "ca_bundle_hash": "sha256:" + "6" * 64,
        "build_attestation_id": "artifact_" + "7" * 64,
        "build_attestation_hash": "sha256:" + "7" * 64,
        "import_smoke_attestation_id": "artifact_" + "8" * 64,
        "import_smoke_attestation_hash": "sha256:" + "8" * 64,
        "sbom_path": "sbom.spdx.json",
        "sbom_hash": "sha256:" + "3" * 64,
        "license_manifest_path": "license-manifest.json",
        "license_manifest_hash": "sha256:" + "4" * 64,
        "smoke_contract_path": "smoke.json",
        "smoke_contract_hash": "sha256:" + "5" * 64,
        "package_versions": {"citylearn": "2.5.0"},
        "repository_commits": {"citylearn": "29062af6d077409e1c37a3e53a6cac30fd4d02bc"},
        "dataset_revisions": {},
        "model_revisions": {},
        "hardware_contract": "cpu",
        "capabilities": ["remote_env.citylearn.v1"],
        "conflict_groups": ["citylearn-runtime"],
        "egress_allowlist": [],
        "credential_ids": [],
        "read_mounts": [],
        "write_mounts": [],
        "protocol_edges": [],
        "restricted": False,
        "build_enabled": True,
        "publishable": True,
    }


def test_runtime_profile_manifest_has_closed_frozen_schema_and_stable_hash() -> None:
    provenance = _provenance()
    model = provenance.RuntimeProfileManifest
    expected_fields = set(_profile_payload())

    assert set(model.model_json_schema()["properties"]) == expected_fields
    first = model.model_validate(_profile_payload())
    reordered = dict(reversed(tuple(_profile_payload().items())))
    second = model.model_validate(reordered)
    expected_hash = (
        "sha256:" + sha256(canonical_json_bytes(_profile_payload())).hexdigest()
    )

    assert first.manifest_hash == expected_hash
    assert second.manifest_hash == expected_hash
    assert _SHA256_PATTERN.fullmatch(first.manifest_hash)

    with pytest.raises(ValidationError):
        model.model_validate({**_profile_payload(), "unregistered_field": True})
    with pytest.raises(ValidationError):
        model.model_validate({**_profile_payload(), "python_version": 312})
    with pytest.raises(ValidationError):
        model.model_validate({**_profile_payload(), "lockfile_path": "./uv.lock"})
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                **_profile_payload(),
                "image_status": "recipe_frozen",
            }
        )
    with pytest.raises(ValidationError):
        model.model_validate({**_profile_payload(), "image_digest": None})
    with pytest.raises(ValidationError):
        first.profile_id = "env-smacv2"


@pytest.mark.parametrize("field_name", tuple(_RUNTIME_SECURITY_IDENTITY_VALUES))
def test_runtime_security_identity_fields_are_strict_frozen_sorted_unique_and_hashed(
    field_name: str,
) -> None:
    model = _provenance().RuntimeProfileManifest
    baseline = model.model_validate(_profile_payload(), strict=True)
    values = _RUNTIME_SECURITY_IDENTITY_VALUES[field_name]
    payload = {**_profile_payload(), field_name: list(values)}

    manifest = model.model_validate(payload, strict=True)

    assert type(getattr(manifest, field_name)) is tuple
    assert getattr(manifest, field_name) == values
    assert manifest.manifest_hash != baseline.manifest_hash
    with pytest.raises(
        ValidationError, match=f"{field_name} must be sorted and unique"
    ):
        model.model_validate(
            {**_profile_payload(), field_name: list(reversed(values))},
            strict=True,
        )
    with pytest.raises(
        ValidationError, match=f"{field_name} must be sorted and unique"
    ):
        model.model_validate(
            {**_profile_payload(), field_name: [values[0], values[0]]},
            strict=True,
        )
    with pytest.raises(ValidationError):
        model.model_validate(
            {**_profile_payload(), field_name: [1]},
            strict=True,
        )
    with pytest.raises(ValidationError):
        setattr(manifest, field_name, ())


def test_every_runtime_profile_manifest_declares_security_identity_fields() -> None:
    required_fields = set(_RUNTIME_SECURITY_IDENTITY_VALUES)

    for profile_id in sorted(_PROFILE_IDS):
        payload = json.loads(
            (_PROFILES_ROOT / profile_id / "profile.json").read_text(encoding="utf-8")
        )
        assert required_fields <= set(payload), profile_id


def test_verifier_rejects_rehashed_security_identity_policy_drift(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    for relative_path in ("profiles", "references"):
        shutil.copytree(
            _REPOSITORY_ROOT / relative_path,
            repository / relative_path,
            ignore=shutil.ignore_patterns(".venv", "__pycache__"),
        )
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_REPOSITORY_ROOT / filename, repository / filename)

    provenance = _provenance()
    profile_path = repository / "profiles" / "core" / "profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    original = provenance.RuntimeProfileManifest.model_validate(payload, strict=True)
    payload["egress_allowlist"] = sorted(
        [*payload["egress_allowlist"], "forbidden.example.invalid:443"]
    )
    mutated = provenance.RuntimeProfileManifest.model_validate(payload, strict=True)
    assert mutated.manifest_hash != original.manifest_hash
    profile_path.write_bytes(canonical_json_bytes(payload))

    report = provenance.verify_provenance(repository)

    assert report.valid is False
    assert "core: central frozen profile identity mismatch" in report.errors
    assert any(
        error.startswith("core:")
        and "egress_allowlist differs from central frozen policy" in error
        for error in report.errors
    ), report.errors


def test_attached_unverified_profile_cannot_be_publishable() -> None:
    payload = {
        **_profile_payload(),
        "profile_id": "llm-qwen36-vllm",
        "image_status": "attached_unverified",
        "image_digest": None,
        "platform": None,
        "libc_version": None,
        "openssl_version": None,
        "ca_bundle_hash": None,
        "build_attestation_id": None,
        "build_attestation_hash": None,
        "import_smoke_attestation_id": None,
        "import_smoke_attestation_hash": None,
        "build_enabled": False,
        "restricted": False,
        "publishable": True,
    }

    with pytest.raises(ValidationError):
        _provenance().RuntimeProfileManifest.model_validate(payload)


def test_repository_contains_exact_isolated_profile_catalog_and_required_files() -> (
    None
):
    actual_ids = (
        {path.name for path in _PROFILES_ROOT.iterdir() if path.is_dir()}
        if _PROFILES_ROOT.is_dir()
        else set()
    )
    assert actual_ids == _PROFILE_IDS

    for profile_id in sorted(_PROFILE_IDS):
        profile_root = _PROFILES_ROOT / profile_id
        actual_files = {path.name for path in profile_root.iterdir() if path.is_file()}
        assert _PROFILE_FILES <= actual_files, profile_id


def test_profile_catalog_hashes_real_metadata_files_and_uv_locks() -> None:
    provenance = _provenance()
    profiles = provenance.load_runtime_profiles(_PROFILES_ROOT)
    assert {profile.profile_id for profile in profiles} == _PROFILE_IDS

    for profile in profiles:
        profile_root = _PROFILES_ROOT / profile.profile_id
        assert profile.lockfile_path == "uv.lock"
        assert profile.containerfile_path == "Containerfile"
        assert profile.sbom_path == "sbom.spdx.json"
        assert profile.license_manifest_path == "license-manifest.json"
        assert profile.smoke_contract_path == "smoke.json"

        for relative_path, recorded_hash in (
            (profile.lockfile_path, profile.lock_hash),
            (profile.sbom_path, profile.sbom_hash),
            (profile.license_manifest_path, profile.license_manifest_hash),
            (profile.smoke_contract_path, profile.smoke_contract_hash),
        ):
            expected_hash = (
                "sha256:"
                + sha256((profile_root / relative_path).read_bytes()).hexdigest()
            )
            assert recorded_hash == expected_hash, (profile.profile_id, relative_path)

        build_context = {
            "domain": "AutoMarkov-Runtime-Profile-Build-Context-v2",
            "files": [
                {
                    "mode": (
                        "0755"
                        if stat.S_IMODE((profile_root / relative_path).stat().st_mode)
                        & 0o111
                        else "0644"
                    ),
                    "path": relative_path,
                    "sha256": "sha256:"
                    + sha256((profile_root / relative_path).read_bytes()).hexdigest(),
                }
                for relative_path in profile.build_context_files
            ],
        }
        expected_build_context_hash = (
            "sha256:" + sha256(canonical_json_bytes(build_context)).hexdigest()
        )
        assert profile.build_context_hash == expected_build_context_hash

        dockerignore_lines = tuple(
            line
            for line in (profile_root / ".dockerignore")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        )
        assert dockerignore_lines == (
            "*",
            *(f"!{relative_path}" for relative_path in profile.build_context_files),
        )

        lock = tomllib.loads((profile_root / "uv.lock").read_text(encoding="utf-8"))
        assert lock["version"] >= 1
        assert lock["revision"] >= 1
        assert lock["package"], profile.profile_id


def test_profile_image_state_never_claims_an_unbuilt_oci_digest() -> None:
    profiles = {
        profile.profile_id: profile
        for profile in _provenance().load_runtime_profiles(_PROFILES_ROOT)
    }

    buildable = {
        profile_id for profile_id, profile in profiles.items() if profile.build_enabled
    }
    assert buildable == _PROFILE_IDS - {
        "llm-qwen36-vllm",
        "replication-agent2world-restricted",
    }
    assert {
        profile.image_status for profile in profiles.values() if profile.build_enabled
    } == {"recipe_frozen"}
    assert profiles["llm-qwen36-vllm"].image_status == "attached_unverified"
    assert (
        profiles["replication-agent2world-restricted"].image_status
        == "restricted_disabled"
    )
    assert all(profile.image_digest is None for profile in profiles.values())
    assert all(profile.platform is None for profile in profiles.values())
    assert all(profile.build_attestation_hash is None for profile in profiles.values())
    assert {profile.target_platform for profile in profiles.values()} == {"linux/amd64"}


def test_selected_lock_artifact_is_exact_for_python_and_linux_amd64() -> None:
    provenance = _provenance()
    package = {
        "name": "example",
        "version": "1.0.0",
        "source": {"registry": "https://pypi.org/simple"},
        "sdist": {
            "url": "https://files.pythonhosted.org/example-1.0.0.tar.gz",
            "hash": "sha256:" + "1" * 64,
        },
        "wheels": [
            {
                "url": "https://files.pythonhosted.org/example-1.0.0-cp311-cp311-macosx_12_0_arm64.whl",
                "hash": "sha256:" + "2" * 64,
            },
            {
                "url": "https://files.pythonhosted.org/example-1.0.0-py3-none-any.whl",
                "hash": "sha256:" + "3" * 64,
            },
            {
                "url": "https://files.pythonhosted.org/example-1.0.0-cp311-cp311-manylinux2014_x86_64.whl",
                "hash": "sha256:" + "4" * 64,
            },
        ],
    }

    assert provenance._select_lock_artifact(
        package,
        python_version="3.11.13",
        target_platform="linux/amd64",
    ) == (
        "https://files.pythonhosted.org/example-1.0.0-cp311-cp311-manylinux2014_x86_64.whl",
        "sha256:" + "4" * 64,
    )
    assert provenance._select_lock_artifact(
        package,
        python_version="3.12.11",
        target_platform="linux/amd64",
    ) == (
        "https://files.pythonhosted.org/example-1.0.0-py3-none-any.whl",
        "sha256:" + "3" * 64,
    )

    incompatible = {
        **package,
        "wheels": package["wheels"][:1],
    }
    assert provenance._select_lock_artifact(
        incompatible,
        python_version="3.11.13",
        target_platform="linux/amd64",
    ) == (
        "https://files.pythonhosted.org/example-1.0.0.tar.gz",
        "sha256:" + "1" * 64,
    )


def test_target_lock_closure_applies_markers_and_requested_extras() -> None:
    provenance = _provenance()
    rllib_lock = tomllib.loads(
        (_PROFILES_ROOT / "rllib-core" / "uv.lock").read_text(encoding="utf-8")
    )
    rllib_packages = provenance._target_installation_package_names(
        rllib_lock,
        python_version="3.11.13",
        target_platform="linux/amd64",
    )
    assert {
        "dm-tree",
        "pandas",
        "pyarrow",
        "scipy",
        "tensorboardx",
    } <= rllib_packages

    authoring_lock = tomllib.loads(
        (_PROFILES_ROOT / "authoring" / "uv.lock").read_text(encoding="utf-8")
    )
    authoring_packages = provenance._target_installation_package_names(
        authoring_lock,
        python_version="3.11.13",
        target_platform="linux/amd64",
    )
    assert "pywin32" not in authoring_packages


def test_conflicting_simulator_dependencies_have_single_process_owners() -> None:
    profiles = {
        profile.profile_id: profile
        for profile in _provenance().load_runtime_profiles(_PROFILES_ROOT)
    }

    for package, expected_owner in _CONFLICT_PACKAGE_OWNERS.items():
        owners = {
            profile.profile_id
            for profile in profiles.values()
            if package in profile.package_versions
            or package in profile.repository_commits
        }
        assert owners == {expected_owner}, package

    for profile_id, expected_commits in _EXPECTED_REPOSITORY_COMMITS.items():
        profile = profiles[profile_id]
        for repository, commit in expected_commits.items():
            assert profile.repository_commits[repository] == commit

    assert profiles["env-citylearn"].package_versions["citylearn"] == "2.5.0"
    assert profiles["env-metadrive"].package_versions["metadrive-simulator"] == "0.4.3"
    assert "metadrive" not in profiles["env-metadrive"].package_versions


def test_smoke_contracts_are_static_profile_owned_import_plans() -> None:
    smoke_contracts: dict[str, dict[str, object]] = {}
    for profile_id in sorted(_PROFILE_IDS):
        smoke_path = _PROFILES_ROOT / profile_id / "smoke.json"
        smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
        assert set(smoke) == {
            "schema_version",
            "profile_id",
            "enabled",
            "imports",
            "forbidden_imports",
            "failure_mode",
            "preflight",
        }
        assert smoke["schema_version"] == "automarkov.profile-smoke.v1"
        assert smoke["profile_id"] == profile_id
        assert smoke["failure_mode"] == "fail_closed"
        assert isinstance(smoke["imports"], list)
        assert isinstance(smoke["forbidden_imports"], list)
        smoke_contracts[profile_id] = smoke

    assert "citylearn" in cast(list[str], smoke_contracts["env-citylearn"]["imports"])
    assert "smacv2" in cast(list[str], smoke_contracts["env-smacv2"]["imports"])
    assert {"metadrive", "scenarionet"} <= set(
        cast(list[str], smoke_contracts["env-metadrive"]["imports"])
    )
    assert {
        "citylearn",
        "metadrive",
        "open_spiel",
        "ray",
        "scenarionet",
        "smacv2",
        "tavily",
        "vllm",
    } <= set(cast(list[str], smoke_contracts["core"]["forbidden_imports"]))

    restricted = smoke_contracts["replication-agent2world-restricted"]
    assert restricted["enabled"] is False
    assert restricted["imports"] == []
    attached = smoke_contracts["llm-qwen36-vllm"]
    assert attached["enabled"] is False
    assert attached["imports"] == []
    assert smoke_contracts["rllib-taxi-synthesis"]["preflight"] == "taxi_deny_v1"
    assert all(
        smoke["preflight"] is None
        for profile_id, smoke in smoke_contracts.items()
        if profile_id != "rllib-taxi-synthesis"
    )


def test_taxi_recipe_verify_is_repeatable_and_zero_write_without_env_guard(
    tmp_path: Path,
) -> None:
    profile_root = _PROFILES_ROOT / "rllib-taxi-synthesis"
    hardener = profile_root / "taxi_deny.py"
    site_packages = tmp_path / "site-packages"
    taxi_root = site_packages / "gymnasium" / "envs" / "toy_text"
    cache_root = tmp_path / "uv-cache"
    taxi_root.mkdir(parents=True)
    cache_root.mkdir()
    for package_root in (
        site_packages / "gymnasium",
        site_packages / "gymnasium" / "envs",
        taxi_root,
    ):
        (package_root / "__init__.py").write_text("", encoding="utf-8")
    (site_packages / "gymnasium" / "envs" / "__init__.py").write_text(
        'register(\n    id="Taxi-v3",\n'
        '    entry_point="gymnasium.envs.toy_text.taxi:TaxiEnv",\n'
        "    reward_threshold=8,  # optimum = 8.46\n"
        "    max_episode_steps=200,\n)\n\n",
        encoding="utf-8",
    )
    (taxi_root / "__init__.py").write_text(
        "from gymnasium.envs.toy_text.taxi import TaxiEnv\n",
        encoding="utf-8",
    )
    (taxi_root / "taxi.py").write_text("GOLD = True\n", encoding="utf-8")
    pycache = taxi_root / "__pycache__"
    pycache.mkdir()
    (pycache / "taxi.cpython-311.pyc").write_bytes(b"gold-bytecode")
    (cache_root / "gymnasium-1.2.2-py3-none-any.whl").write_bytes(b"gold-wheel")

    common = [
        sys.executable,
        str(hardener),
        "--site-packages",
        str(site_packages),
        "--cache-root",
        str(cache_root),
    ]
    environment = os.environ.copy()
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)

    def run(action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*common, action],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )

    def tree_state() -> tuple[tuple[str, bytes | None], ...]:
        return tuple(
            (
                path.relative_to(tmp_path).as_posix(),
                path.read_bytes() if path.is_file() else None,
            )
            for path in sorted(tmp_path.rglob("*"))
        )

    hardened = run("harden")
    hardened_state = tree_state()
    verified_once = run("verify")
    verified_once_state = tree_state()
    verified_twice = run("verify")
    verified_twice_state = tree_state()

    completed = (hardened, verified_once, verified_twice)
    assert [result.returncode for result in completed] == [0, 0, 0], "\n".join(
        result.stderr for result in completed
    )
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.pyc"))
    assert verified_once_state == hardened_state
    assert verified_twice_state == hardened_state

    containerfile = (profile_root / "Containerfile").read_text(encoding="utf-8")
    assert "taxi_deny.py harden" in containerfile
    assert "taxi_deny.py verify" in containerfile


def test_taxi_hardener_rejects_an_uncontrolled_cache_root(tmp_path: Path) -> None:
    profile_root = _PROFILES_ROOT / "rllib-taxi-synthesis"
    for cache_root in ("/", "/tmp/../etc", "/tmp/nonexistent/../../etc"):
        completed = subprocess.run(
            [
                sys.executable,
                str(profile_root / "taxi_deny.py"),
                "--site-packages",
                str(tmp_path / "missing-site-packages"),
                "--cache-root",
                cache_root,
                "harden",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        assert "controlled temporary directory" in completed.stderr


def test_profile_sboms_and_license_manifests_have_machine_readable_identity() -> None:
    for profile_id in sorted(_PROFILE_IDS):
        profile_root = _PROFILES_ROOT / profile_id
        sbom = json.loads((profile_root / "sbom.spdx.json").read_text(encoding="utf-8"))
        licenses = json.loads(
            (profile_root / "license-manifest.json").read_text(encoding="utf-8")
        )

        assert sbom["spdxVersion"] == "SPDX-2.3"
        assert sbom["dataLicense"] == "CC0-1.0"
        assert sbom["name"] == f"automarkov-profile-{profile_id}"
        assert isinstance(sbom["packages"], list)
        assert len({item["SPDXID"] for item in sbom["packages"]}) == len(
            sbom["packages"]
        )
        assert all(
            item["SPDXID"].startswith("SPDXRef-Package-") for item in sbom["packages"]
        )
        assert all(item["filesAnalyzed"] is False for item in sbom["packages"])
        assert all(
            item["licenseConcluded"] == item["licenseDeclared"]
            for item in sbom["packages"]
        )
        assert licenses["schema_version"] == "automarkov.profile-licenses.v1"
        assert licenses["profile_id"] == profile_id
        assert isinstance(licenses["dependencies"], list)

        lock = tomllib.loads((profile_root / "uv.lock").read_text(encoding="utf-8"))
        assert {(item["name"], item["versionInfo"]) for item in sbom["packages"]} == {
            (item["name"], item["version"]) for item in lock["package"]
        }
        locked_dependencies = {
            (item["name"], item["version"])
            for item in lock["package"]
            if "virtual" not in item.get("source", {})
        }
        assert {
            (item["name"], item["version"]) for item in licenses["dependencies"]
        } == locked_dependencies
        assert all(
            item["license"].strip().upper() not in {"", "NOASSERTION", "UNKNOWN"}
            for item in licenses["dependencies"]
        )
        assert all(
            item["licenseDeclared"].strip().upper()
            not in {"", "NOASSERTION", "UNKNOWN"}
            for item in sbom["packages"]
        )
        sbom_licenses = {
            (item["name"], item["versionInfo"]): item["licenseDeclared"]
            for item in sbom["packages"]
        }
        assert all(
            sbom_licenses[(item["name"], item["version"])] == item["license"]
            for item in licenses["dependencies"]
        )
        required_license_refs = {
            match
            for license_name in sbom_licenses.values()
            for match in re.findall(r"\bLicenseRef-[A-Za-z0-9.-]+\b", license_name)
        }
        extracted_licenses = sbom.get("hasExtractedLicensingInfos", [])
        assert [item["licenseId"] for item in extracted_licenses] == sorted(
            required_license_refs
        )
        assert all(item["extractedText"] for item in extracted_licenses)
        assert all(item["name"] for item in extracted_licenses)
        for item in extracted_licenses:
            evidence = json.loads(item["comment"])
            assert set(evidence) == {"domain", "evidence_kind", "sha256"}
            assert evidence["domain"] == "AutoMarkov-SPDX-License-Evidence-v1"
            assert evidence["evidence_kind"] in {
                "full_text",
                "upstream_short_reference",
            }
            assert evidence["sha256"] == (
                "sha256:" + sha256(item["extractedText"].encode("utf-8")).hexdigest()
            )
        assert all(
            item["seeAlsos"]
            and all(source.startswith("https://") for source in item["seeAlsos"])
            for item in extracted_licenses
        )
        expected_license_ref_sources: dict[str, set[str]] = {}
        for dependency in licenses["dependencies"]:
            for license_ref in re.findall(
                r"\bLicenseRef-[A-Za-z0-9.-]+\b",
                dependency["license"],
            ):
                expected_license_ref_sources.setdefault(license_ref, set()).add(
                    dependency["source"]
                )
        assert {
            item["licenseId"]: set(item["seeAlsos"]) for item in extracted_licenses
        } == expected_license_ref_sources
        lock_by_identity = {
            (item["name"], item["version"]): item for item in lock["package"]
        }
        profile_payload = json.loads(
            (profile_root / "profile.json").read_text(encoding="utf-8")
        )
        target_packages = _provenance()._target_installation_package_names(
            lock,
            python_version=profile_payload["python_version"],
            target_platform=profile_payload["target_platform"],
        )
        for item in sbom["packages"]:
            locked = lock_by_identity[(item["name"], item["versionInfo"])]
            source = locked["source"]
            expected_source = canonical_json_bytes(source).decode("utf-8")
            source_kind, source_location = next(iter(source.items()))
            assert item["sourceInfo"] == expected_source
            if source_kind == "registry" and item["name"] in target_packages:
                selected_url, selected_hash = _provenance()._select_lock_artifact(
                    locked,
                    python_version=profile_payload["python_version"],
                    target_platform=profile_payload["target_platform"],
                )
                assert item["downloadLocation"] == selected_url
                expected_hashes = [selected_hash.removeprefix("sha256:")]
            else:
                assert item["downloadLocation"] == (
                    source_location
                    if source_kind == "git" and item["name"] in target_packages
                    else "NOASSERTION"
                )
                expected_hashes = []
            recorded_hashes = [
                checksum["checksumValue"] for checksum in item.get("checksums", [])
            ]
            assert recorded_hashes == expected_hashes
        assert all(
            item["source"].startswith("https://") for item in licenses["dependencies"]
        )


def test_repository_verifier_reports_all_profile_checks_without_building_images() -> (
    None
):
    provenance = _provenance()
    report = provenance.verify_provenance(_REPOSITORY_ROOT)
    assert isinstance(report, provenance.ProvenanceVerificationReport)
    assert report.valid is True
    assert report.profile_count == len(_PROFILE_IDS)
    assert report.errors == ()
    assert {
        "lock_hashes",
        "manifest_hashes",
        "profile_isolation",
        "restricted_exclusion",
        "smoke_contracts",
    } <= set(report.passed_checks)


@pytest.mark.parametrize("mode", (0o664, 0o600), ids=("group-write", "owner-only"))
def test_verifier_normalizes_non_executable_build_context_file_modes(
    tmp_path: Path,
    mode: int,
) -> None:
    repository = tmp_path / "repository"
    for relative_path in ("profiles", "references"):
        shutil.copytree(
            _REPOSITORY_ROOT / relative_path,
            repository / relative_path,
            ignore=shutil.ignore_patterns(".venv", "__pycache__"),
        )
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_REPOSITORY_ROOT / filename, repository / filename)

    provenance = _provenance()
    baseline = provenance.verify_provenance(repository)
    assert baseline.valid is True, baseline.errors

    build_input = repository / "profiles" / "core" / "pyproject.toml"
    profile_path = repository / "profiles" / "core" / "profile.json"
    build_input_bytes = build_input.read_bytes()
    profile_bytes = profile_path.read_bytes()
    assert build_input.stat().st_mode & 0o777 == 0o644

    build_input.chmod(mode)

    assert build_input.stat().st_mode & 0o777 == mode
    assert build_input.read_bytes() == build_input_bytes
    assert profile_path.read_bytes() == profile_bytes
    report = provenance.verify_provenance(repository)
    assert report.valid is True, report.errors


def test_verifier_rejects_build_context_file_mode_drift(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    for relative_path in ("profiles", "references"):
        shutil.copytree(
            _REPOSITORY_ROOT / relative_path,
            repository / relative_path,
            ignore=shutil.ignore_patterns(".venv", "__pycache__"),
        )
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_REPOSITORY_ROOT / filename, repository / filename)

    provenance = _provenance()
    baseline = provenance.verify_provenance(repository)
    assert baseline.valid is True, baseline.errors

    build_input = repository / "profiles" / "core" / "pyproject.toml"
    profile_path = repository / "profiles" / "core" / "profile.json"
    build_input_bytes = build_input.read_bytes()
    profile_bytes = profile_path.read_bytes()
    assert build_input.stat().st_mode & 0o777 == 0o644

    build_input.chmod(0o755)

    assert build_input.stat().st_mode & 0o777 == 0o755
    assert build_input.read_bytes() == build_input_bytes
    assert profile_path.read_bytes() == profile_bytes
    report = provenance.verify_provenance(repository)
    assert report.valid is False
    assert any(
        error.startswith("core: build context")
        and ("mode" in error or "hash mismatch" in error)
        for error in report.errors
    ), report.errors


def test_verify_provenance_cli_uses_the_same_closed_report() -> None:
    completed = subprocess.run(
        [
            "python",
            "-m",
            "automarkov",
            "verify-provenance",
            "--repository-root",
            str(_REPOSITORY_ROOT),
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    report = _provenance().ProvenanceVerificationReport.model_validate(
        payload,
        strict=True,
    )
    assert report.valid is True
    assert report.profile_count == len(_PROFILE_IDS)


def test_cli_does_not_expose_planned_experiment_commands() -> None:
    completed = subprocess.run(
        ["python", "-m", "automarkov", "--help"],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "exp-" not in completed.stdout


def test_verifier_rejects_semantic_summary_drift_and_unresolved_built_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(
        _PROFILES_ROOT,
        repository / "profiles",
        ignore=shutil.ignore_patterns(".venv", "__pycache__"),
    )
    (repository / "references").mkdir()
    shutil.copy2(
        _REPOSITORY_ROOT / "references" / "manifest.yaml",
        repository / "references" / "manifest.yaml",
    )
    shutil.copy2(_REPOSITORY_ROOT / "pyproject.toml", repository / "pyproject.toml")
    shutil.copy2(_REPOSITORY_ROOT / "uv.lock", repository / "uv.lock")

    core_path = repository / "profiles" / "core" / "profile.json"
    original = core_path.read_bytes()
    payload = json.loads(original)
    payload["package_versions"]["cryptography"] = "0.0.0"
    core_path.write_text(json.dumps(payload), encoding="utf-8")
    drift = _provenance().verify_provenance(repository)
    assert drift.valid is False
    assert any("package summary does not match lock" in error for error in drift.errors)

    core_path.write_bytes(original)
    payload = json.loads(original)
    built_fields = _profile_payload()
    payload.update(
        {
            field: built_fields[field]
            for field in (
                "build_attestation_hash",
                "build_attestation_id",
                "ca_bundle_hash",
                "image_digest",
                "image_status",
                "import_smoke_attestation_hash",
                "import_smoke_attestation_id",
                "libc_version",
                "openssl_version",
                "platform",
            )
        }
    )
    core_path.write_text(json.dumps(payload), encoding="utf-8")
    built = _provenance().verify_provenance(repository)
    assert built.valid is False
    assert any("ArtifactRepository/head" in error for error in built.errors)

    core_path.write_bytes(original)
    sbom_path = repository / "profiles" / "core" / "sbom.spdx.json"
    original_sbom = sbom_path.read_bytes()
    sbom = json.loads(original_sbom)
    package = next(item for item in sbom["packages"] if item.get("checksums"))
    package["checksums"][0]["checksumValue"] = "0" * 64
    package["sourceInfo"] = '{"registry":"https://example.invalid/simple"}'
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    payload = json.loads(original)
    payload["sbom_hash"] = "sha256:" + sha256(sbom_path.read_bytes()).hexdigest()
    core_path.write_text(json.dumps(payload), encoding="utf-8")
    substitution = _provenance().verify_provenance(repository)
    assert substitution.valid is False
    assert any(
        "SBOM source does not match lock" in error for error in substitution.errors
    )
    assert any(
        "SBOM checksum does not match lock" in error for error in substitution.errors
    )

    license_profile_path = repository / "profiles" / "rllib-core" / "profile.json"
    license_profile = json.loads(license_profile_path.read_bytes())
    sbom_path = repository / "profiles" / "rllib-core" / "sbom.spdx.json"
    sbom = json.loads(sbom_path.read_bytes())
    license_info = sbom["hasExtractedLicensingInfos"][0]
    license_info["extractedText"] += " forged"
    evidence = json.loads(license_info["comment"])
    evidence["sha256"] = (
        "sha256:" + sha256(license_info["extractedText"].encode("utf-8")).hexdigest()
    )
    license_info["comment"] = canonical_json_bytes(evidence).decode("utf-8")
    sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
    license_profile["sbom_hash"] = (
        "sha256:" + sha256(sbom_path.read_bytes()).hexdigest()
    )
    license_profile_path.write_text(json.dumps(license_profile), encoding="utf-8")
    forged_license = _provenance().verify_provenance(repository)
    assert forged_license.valid is False
    assert any(
        "LicenseRef evidence is not frozen" in error for error in forged_license.errors
    )

    manifest_path = repository / "references" / "manifest.yaml"
    manifests = json.loads(manifest_path.read_bytes())
    smacv2 = next(item for item in manifests if item["resource_id"] == "smacv2")
    smacv2["license"] = "Apache-2.0"
    manifest_path.write_text(json.dumps(manifests), encoding="utf-8")
    forged_upstream_license = _provenance().verify_provenance(repository)
    assert forged_upstream_license.valid is False
    assert any(
        "smacv2: upstream license does not match dependency license evidence" in error
        for error in forged_upstream_license.errors
    )
