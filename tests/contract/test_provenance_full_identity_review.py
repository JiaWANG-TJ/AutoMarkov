from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from automarkov.provenance import (
    ProvenanceVerificationReport,
    load_runtime_profiles,
    load_upstream_manifests,
    verify_provenance,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_UPSTREAM_MANIFEST_HASHES = {
    "agent2world": "sha256:2bc57a316b771cb4600d9817fa21852e50b17acf15a8a3905dddd9760a721c27",
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
_PROFILE_MANIFEST_HASHES = {
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
    "replication-agent2world-restricted": "sha256:2e844112c8c2832801a5dea772427376b7345c4853447c69b7ba25f25de2eacc",
    "retrieval-tavily": "sha256:6e87fc2cff550546c728bebd7ad7ccff9fbd826a49035152fe397d0887238336",
    "rllib-core": "sha256:d655179755175ee164710a92dbf3b6220b80fff919a6ea67483b59b1de389428",
    "rllib-taxi-synthesis": "sha256:2c29507240d25c1a601c2fc66f424ee00ce85cc93eb131595fc291edd68f1474",
    "runner-control": "sha256:6f210691b684fd869bcb06669440cf3ba456d6497637a5ab8db69a58c5778c9a",
    "sealed-env-taxi-gold": "sha256:66dc4b6fd4fa125c3781fbbf56dbaccb739ebc454583eb861c3a6acbacb9eb2d",
    "sealed-evaluator-rllib": "sha256:afe6be91902305fab8dc63157ad4a88e2fa908d8acbd0f223ba1ff4446143d05",
}


@pytest.fixture
def repository_copy(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative_path in ("profiles", "references"):
        shutil.copytree(
            _REPOSITORY_ROOT / relative_path,
            repository / relative_path,
            ignore=shutil.ignore_patterns(".venv", "__pycache__"),
        )
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_REPOSITORY_ROOT / filename, repository / filename)

    baseline = verify_provenance(repository)
    assert baseline.valid is True, baseline.errors
    return repository


def _read_json_array(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_bytes())
    assert type(payload) is list
    assert all(type(item) is dict for item in payload)
    return cast(list[dict[str, object]], payload)


def _read_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_bytes())
    assert type(payload) is dict
    return cast(dict[str, object], payload)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _assert_central_identity_rejected(
    report: ProvenanceVerificationReport,
    identity: str,
    kind: str,
) -> None:
    assert report.valid is False
    expected = f"{identity}: central frozen {kind} identity"
    assert any(expected in error.lower() for error in report.errors), report.errors


def test_all_upstream_manifest_identities_are_centrally_frozen(
    repository_copy: Path,
) -> None:
    upstreams = load_upstream_manifests(
        _REPOSITORY_ROOT / "references" / "manifest.yaml"
    )
    assert {item.resource_id: item.manifest_hash for item in upstreams} == (
        _UPSTREAM_MANIFEST_HASHES
    )
    attacks = (
        ("bytesized32", "commit", "0" * 40),
        ("gymnasium", "dependency_profile", "rllib-taxi-synthesis"),
        ("torch", "license", "MIT"),
    )
    catalog_path = repository_copy / "references" / "manifest.yaml"

    for resource_id, field, replacement in attacks:
        catalog = _read_json_array(_REPOSITORY_ROOT / "references" / "manifest.yaml")
        upstream = next(item for item in catalog if item["resource_id"] == resource_id)
        upstream[field] = replacement
        _write_json(catalog_path, catalog)

        report = verify_provenance(repository_copy)

        _assert_central_identity_rejected(report, resource_id, "upstream")


def test_upstream_catalog_requires_the_exact_registered_set(
    repository_copy: Path,
) -> None:
    catalog_path = repository_copy / "references" / "manifest.yaml"
    catalog = _read_json_array(catalog_path)
    _write_json(
        catalog_path,
        [item for item in catalog if item["resource_id"] != "bytesized32"],
    )

    report = verify_provenance(repository_copy)

    assert report.valid is False
    assert "upstream catalog does not contain the exact registered set" in (
        error.lower() for error in report.errors
    ), report.errors


def test_all_runtime_profile_manifest_identities_are_centrally_frozen(
    repository_copy: Path,
) -> None:
    profiles = load_runtime_profiles(_REPOSITORY_ROOT / "profiles")
    assert {item.profile_id: item.manifest_hash for item in profiles} == (
        _PROFILE_MANIFEST_HASHES
    )
    attacks: tuple[tuple[str, str, object], ...] = (
        ("authoring", "hardware_contract", "gpu"),
        ("authoring", "conflict_groups", ["authoring-runtime-v2"]),
        ("authoring", "dataset_revisions", {"authoring-dataset": "revision-v2"}),
        ("authoring", "model_revisions", {"authoring-model": "revision-v2"}),
        (
            "llm-qwen36-vllm",
            "model_revisions",
            {"Qwen/Qwen3.6-35B-A3B": "0" * 40},
        ),
    )

    for profile_id, field, replacement in attacks:
        source_path = _REPOSITORY_ROOT / "profiles" / profile_id / "profile.json"
        target_path = repository_copy / "profiles" / profile_id / "profile.json"
        profile = _read_json_object(source_path)
        profile[field] = replacement
        _write_json(target_path, profile)

        report = verify_provenance(repository_copy)

        _assert_central_identity_rejected(report, profile_id, "profile")
        shutil.copy2(source_path, target_path)
