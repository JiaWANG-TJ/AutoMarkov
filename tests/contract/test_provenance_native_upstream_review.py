from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from automarkov.provenance import UpstreamManifest, verify_provenance

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPOSITORY_ROOT / "references" / "manifest.yaml"
_GYMNASIUM_FROZEN_IDENTITY = {
    "repository": "https://github.com/Farama-Foundation/Gymnasium",
    "commit": "a923da5d4415a1aa5195d99341069da5e16deed7",
    "license_file_hash": (
        "sha256:7dacaa9772e856aee6943b32ef663d3634d91d72ec7bbc74d136943673f91e18"
    ),
}


def _catalog_payload() -> list[dict[str, object]]:
    payload = json.loads(_MANIFEST_PATH.read_bytes())
    assert type(payload) is list
    assert all(type(item) is dict for item in payload)
    return payload


def _upstream_payload(resource_id: str) -> dict[str, object]:
    return next(
        item for item in _catalog_payload() if item["resource_id"] == resource_id
    )


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


def test_active_upstream_rejects_external_cache_install_bypass() -> None:
    payload = {
        **_upstream_payload("gymnasium"),
        "install_mode": "external_cache",
        "checksums": [],
    }

    with pytest.raises(ValidationError):
        UpstreamManifest.model_validate(payload, strict=True)


def test_active_pip_upstream_identity_is_bound_to_authoritative_frozen_bom(
    repository_copy: Path,
) -> None:
    frozen = _upstream_payload("gymnasium")
    assert {
        field: frozen[field] for field in _GYMNASIUM_FROZEN_IDENTITY
    } == _GYMNASIUM_FROZEN_IDENTITY
    attacks = (
        ("repository", "https://github.com/ray-project/ray"),
        ("commit", "0" * 40),
        ("license_file_hash", "sha256:" + "0" * 64),
    )
    accepted_attacks: list[str] = []

    for field, replacement in attacks:
        catalog = _catalog_payload()
        gymnasium = next(item for item in catalog if item["resource_id"] == "gymnasium")
        gymnasium[field] = replacement
        (repository_copy / "references" / "manifest.yaml").write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        report = verify_provenance(repository_copy)
        if report.valid:
            accepted_attacks.append(field)

    assert accepted_attacks == []


def test_upstream_manifest_rejects_malformed_https_repository() -> None:
    malformed_repositories = (
        "https://[invalid",
        "https://github.com:invalid/Farama-Foundation/Gymnasium",
    )
    accepted_repositories: list[str] = []

    for repository in malformed_repositories:
        try:
            UpstreamManifest.model_validate(
                {**_upstream_payload("gymnasium"), "repository": repository},
                strict=True,
            )
        except ValidationError:
            continue
        accepted_repositories.append(repository)

    assert accepted_repositories == []


def test_verifier_reports_malformed_upstream_url_without_traceback(
    repository_copy: Path,
) -> None:
    catalog = _catalog_payload()
    gymnasium = next(item for item in catalog if item["resource_id"] == "gymnasium")
    gymnasium["repository"] = "https://[invalid"
    (repository_copy / "references" / "manifest.yaml").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = verify_provenance(repository_copy)

    assert report.valid is False
    assert any(
        "upstream catalog" in error.lower() and "repository" in error.lower()
        for error in report.errors
    ), report.errors
