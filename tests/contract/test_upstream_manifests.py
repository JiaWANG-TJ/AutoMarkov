from __future__ import annotations

import importlib
import json
import subprocess
import tomllib
from hashlib import sha256
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from automarkov.domain.canonical import MAX_CANONICAL_DOCUMENT_BYTES, canonical_json_bytes

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPOSITORY_ROOT / "references" / "manifest.yaml"
_AGENT2WORLD_COMMIT = "1330f3cde9509f05d204a255f0f7f43208515dce"
_OASIS_COMMIT = "e97a1d83761605a24a7dc91fa4d4e9defffa7e23"

_PINNED_UPSTREAMS = {
    "citylearn": ("2.5.0", "29062af6d077409e1c37a3e53a6cac30fd4d02bc"),
    "gymnasium-1-3": ("1.3.0", "53bf3e9a884783eb72ad3fc8b15780914c97c3e1"),
    "metadrive": ("0.4.3", "5bf8ea8909c4643a4099a250e6f5fb89c695d8b4"),
    "ray-rllib": ("2.56.1", "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a"),
    "scenarionet": (None, "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170"),
    "smacv2": (None, "577ab5a2cff2391f8df582da5731ea9cd6adf3c6"),
}
_REQUIRED_RESOURCE_IDS = {
    "agent2world",
    "bytesized32",
    "camel-ai",
    "camel-oasis",
    "citylearn",
    "code-world-models",
    "gymnasium",
    "gymnasium-1-3",
    "llamafactory",
    "metadrive",
    "minigrid",
    "mpe2",
    "open-spiel",
    "pettingzoo",
    "pydantic",
    "qwen36-35b-a3b",
    "ray-rllib",
    "safetensors",
    "sc2-assets",
    "scenarionet",
    "smacv2",
    "swanlab",
    "tavily-python",
    "text2world",
    "torch",
    "unified-planning",
    "vllm",
}


def _provenance() -> ModuleType:
    try:
        return importlib.import_module("automarkov.security.provenance")
    except ModuleNotFoundError:
        pytest.fail("T04 requires the public automarkov.security.provenance deep module")


def _upstream_payload() -> dict[str, object]:
    return {
        "schema_version": "automarkov.upstream-manifest.v2",
        "resource_id": "ray-rllib",
        "repository": "https://github.com/ray-project/ray",
        "commit": "936f0d7d49d9da8ac1a9f04cc8a89faf2cb3c42a",
        "release": "2.56.1",
        "resolution_status": "pinned",
        "integration_status": "active",
        "purpose": "RLlib training backend",
        "license": "Apache-2.0",
        "license_file_hash": "sha256:" + "a" * 64,
        "redistribution_policy": "permitted",
        "install_mode": "pip",
        "dependency_profile": "rllib-core",
        "data_assets": [],
        "checksums": ["sha256:" + "b" * 64],
        "citation": "Ray 2.56.1",
    }


def test_upstream_manifest_has_exact_section_14_schema_and_stable_hash() -> None:
    model = _provenance().UpstreamManifest
    expected_fields = set(_upstream_payload())
    assert set(model.model_json_schema()["properties"]) == expected_fields

    first = model.model_validate(_upstream_payload())
    reordered = dict(reversed(tuple(_upstream_payload().items())))
    second = model.model_validate(reordered)
    expected_hash = (
        "sha256:" + sha256(canonical_json_bytes(_upstream_payload())).hexdigest()
    )

    assert first.manifest_hash == expected_hash
    assert second.manifest_hash == expected_hash
    with pytest.raises(ValidationError):
        model.model_validate({**_upstream_payload(), "branch": "main"})
    with pytest.raises(ValidationError):
        model.model_validate({**_upstream_payload(), "commit": "main"})
    with pytest.raises(ValidationError):
        model.model_validate({**_upstream_payload(), "license": ""})
    with pytest.raises(ValidationError):
        model.model_validate({**_upstream_payload(), "checksums": []})
    with pytest.raises(ValidationError):
        model.model_validate(
            {**_upstream_payload(), "commit": None, "release": "latest"}
        )
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                **_upstream_payload(),
                "commit": None,
                "release": "asset-blocked-unresolved",
                "resolution_status": "blocked_unresolved",
                "redistribution_policy": "prohibited",
                "install_mode": "dataset_download",
                "checksums": [],
            }
        )


def test_reference_catalog_covers_authoritative_upstreams_and_exact_pins() -> None:
    manifests = _provenance().load_upstream_manifests(_MANIFEST_PATH)
    by_id = {manifest.resource_id: manifest for manifest in manifests}
    assert len(by_id) == len(manifests)
    assert _REQUIRED_RESOURCE_IDS <= set(by_id)

    for resource_id, (release, commit) in _PINNED_UPSTREAMS.items():
        manifest = by_id[resource_id]
        assert manifest.commit == commit
        assert manifest.release == release
        assert manifest.license
        assert manifest.license_file_hash.startswith("sha256:")
        assert manifest.dependency_profile

    assert by_id["metadrive"].repository == (
        "https://github.com/metadriverse/metadrive"
    )
    assert "metadrive-simulator" in by_id["metadrive"].purpose
    assert by_id["sc2-assets"].resolution_status == "blocked_unresolved"
    assert by_id["sc2-assets"].commit is None
    assert by_id["sc2-assets"].release is None
    assert by_id["sc2-assets"].checksums == ()
    for manifest in manifests:
        if manifest.integration_status == "active" and manifest.install_mode == "pip":
            assert len(manifest.checksums) == 1
        else:
            assert manifest.checksums == ()

    assert by_id["scenarionet"].install_mode == "git_submodule"
    assert by_id["scenarionet"].checksums == ()
    assert by_id["smacv2"].install_mode == "git_submodule"
    assert by_id["smacv2"].checksums == ()


def test_moving_refs_missing_licenses_and_duplicate_resources_fail_closed(
    tmp_path: Path,
) -> None:
    provenance = _provenance()
    base = _upstream_payload()

    for field, invalid_value in (
        ("commit", "main"),
        ("commit", "v2.56.1"),
        ("license_file_hash", "unknown"),
        ("redistribution_policy", "probably-open"),
        ("install_mode", "curl-pipe-shell"),
    ):
        with pytest.raises(ValidationError):
            provenance.UpstreamManifest.model_validate({**base, field: invalid_value})

    duplicate_catalog = tmp_path / "manifest.yaml"
    duplicate_catalog.write_text(
        "[\n"
        + ",\n".join(json.dumps(base, sort_keys=True) for _ in range(2))
        + "\n]\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError,
        match="duplicate.*resource",
    ):
        provenance.load_upstream_manifests(duplicate_catalog)


def test_manifest_loader_rejects_symlinked_and_oversized_ingress(
    tmp_path: Path,
) -> None:
    provenance = _provenance()
    real_root = tmp_path / "real"
    real_root.mkdir()
    manifest = real_root / "manifest.json"
    manifest.write_text(json.dumps([_upstream_payload()]), encoding="utf-8")
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        provenance.load_upstream_manifests(linked_root / "manifest.json")

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_CANONICAL_DOCUMENT_BYTES + 1))
    with pytest.raises(ValueError, match="bounded ingress"):
        provenance.load_upstream_manifests(oversized)


def test_agent2world_is_auditable_but_excluded_from_build_and_release_inputs() -> None:
    manifests = _provenance().load_upstream_manifests(_MANIFEST_PATH)
    agent2world = next(
        manifest for manifest in manifests if manifest.resource_id == "agent2world"
    )
    assert agent2world.commit == _AGENT2WORLD_COMMIT
    assert agent2world.release is None
    assert agent2world.license == "LicenseRef-Agent2World-Research-Evaluation-Only"
    assert agent2world.license_file_hash.startswith("sha256:")
    assert agent2world.redistribution_policy == "research_evaluation_only"
    assert agent2world.install_mode == "external_cache"
    assert agent2world.dependency_profile == "replication-agent2world-restricted"

    restricted_profile = next(
        profile
        for profile in _provenance().load_runtime_profiles(
            _REPOSITORY_ROOT / "profiles"
        )
        if profile.profile_id == "replication-agent2world-restricted"
    )
    assert restricted_profile.restricted is True
    assert restricted_profile.build_enabled is False
    assert restricted_profile.publishable is False

    tracked = subprocess.run(
        ["git", "ls-files", "external/restricted"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []

    forbidden_build_lines: list[str] = []
    for containerfile in sorted(
        (_REPOSITORY_ROOT / "profiles").glob("*/Containerfile")
    ):
        for line_number, line in enumerate(
            containerfile.read_text(encoding="utf-8").splitlines(), start=1
        ):
            normalized = line.strip().lower()
            if normalized.startswith("#"):
                continue
            if (
                "deepexperience/agent2world" in normalized
                or "external/restricted" in normalized
                or "git clone" in normalized
                and "agent2world" in normalized
            ):
                forbidden_build_lines.append(
                    f"{containerfile.relative_to(_REPOSITORY_ROOT)}:{line_number}"
                )
    assert forbidden_build_lines == []

    forbidden_markers = (
        b"agent2world",
        b"deepexperience/agent2world",
        b"1330f3cde9509f05d204a255f0f7f43208515dce",
        b"external/restricted",
    )
    for profile in _provenance().load_runtime_profiles(_REPOSITORY_ROOT / "profiles"):
        if profile.restricted:
            continue
        for path in (_REPOSITORY_ROOT / "profiles" / profile.profile_id).iterdir():
            if path.is_file():
                lowered = path.read_bytes().lower()
                assert all(marker not in lowered for marker in forbidden_markers), path


def test_oasis_is_deferred_metadata_and_never_enters_a_profile_lock() -> None:
    manifests = _provenance().load_upstream_manifests(_MANIFEST_PATH)
    oasis = next(
        manifest for manifest in manifests if manifest.resource_id == "camel-oasis"
    )

    assert oasis.repository == "https://github.com/camel-ai/oasis"
    assert oasis.release == "0.2.5"
    assert oasis.commit == _OASIS_COMMIT
    assert oasis.license == "Apache-2.0"
    assert oasis.license_file_hash == (
        "sha256:950deb34b1341a0ac95236fae92fe247c318c3a83a62c9ebacbe1882530ab1f6"
    )
    assert oasis.integration_status == "deferred"
    assert oasis.resolution_status == "pinned"
    assert oasis.install_mode == "external_cache"
    assert oasis.checksums == ()
    assert oasis.dependency_profile == "authoring"

    for lock_path in sorted((_REPOSITORY_ROOT / "profiles").glob("*/uv.lock")):
        locked_packages = tomllib.loads(lock_path.read_text(encoding="utf-8")).get(
            "package", []
        )
        assert all(
            package.get("name") != "camel-oasis" for package in locked_packages
        ), lock_path


def test_all_catalog_entries_are_exactly_reconstructable_or_explicitly_restricted() -> (
    None
):
    manifests = _provenance().load_upstream_manifests(_MANIFEST_PATH)
    allowed_policies = {
        "download_only",
        "permitted",
        "prohibited",
        "research_evaluation_only",
    }
    for manifest in manifests:
        if manifest.resolution_status == "blocked_unresolved":
            assert manifest.commit is None
            assert manifest.release is None
            assert manifest.checksums == ()
            assert manifest.redistribution_policy == "prohibited"
        else:
            assert manifest.commit is not None or manifest.release is not None
        assert manifest.license
        assert manifest.license_file_hash.startswith("sha256:")
        assert manifest.redistribution_policy in allowed_policies
        assert manifest.dependency_profile
        if manifest.redistribution_policy != "permitted":
            assert manifest.install_mode in {"dataset_download", "external_cache"}
