from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from automarkov.provenance import ProvenanceVerificationReport, verify_provenance

_ROOT = Path(__file__).resolve().parents[2]
_COPY_IGNORE = shutil.ignore_patterns(".venv", "__pycache__")


@pytest.fixture(scope="module")
def pristine_repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repository = tmp_path_factory.mktemp("native-review") / "repository"
    repository.mkdir()
    for relative_path in ("profiles", "references", "src", "docs", ".github"):
        source = _ROOT / relative_path
        if source.exists():
            shutil.copytree(source, repository / relative_path, ignore=_COPY_IGNORE)
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_ROOT / filename, repository / filename)

    baseline = verify_provenance(repository)
    assert baseline.valid is True, baseline.errors
    return repository


@pytest.fixture
def repository_copy(pristine_repository: Path, tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    shutil.copytree(pristine_repository, repository)
    return repository


def _read_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_bytes())
    assert type(payload) is dict
    return cast(dict[str, object], payload)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _file_hash(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def _rehash_license_evidence(repository: Path, profile_id: str) -> None:
    profile_root = repository / "profiles" / profile_id
    profile_path = profile_root / "profile.json"
    profile = _read_json_object(profile_path)
    for hash_field, path_field in (
        ("sbom_hash", "sbom_path"),
        ("license_manifest_hash", "license_manifest_path"),
    ):
        relative_path = profile[path_field]
        assert type(relative_path) is str
        profile[hash_field] = _file_hash(profile_root / relative_path)
    _write_json(profile_path, profile)


def _dependency(
    payload: dict[str, object],
    collection: str,
    package_name: str,
) -> dict[str, object]:
    entries = payload[collection]
    assert type(entries) is list
    entry = next(
        item
        for item in entries
        if type(item) is dict and item.get("name") == package_name
    )
    return cast(dict[str, object], entry)


def _replace_license_evidence(
    repository: Path,
    *,
    license_expression: str,
    source: str | None = None,
) -> None:
    profile_root = repository / "profiles" / "core"
    sbom_path = profile_root / "sbom.spdx.json"
    sbom = _read_json_object(sbom_path)
    sbom_entry = _dependency(sbom, "packages", "cffi")
    sbom_entry["licenseConcluded"] = license_expression
    sbom_entry["licenseDeclared"] = license_expression
    _write_json(sbom_path, sbom)

    license_path = profile_root / "license-manifest.json"
    licenses = _read_json_object(license_path)
    license_entry = _dependency(licenses, "dependencies", "cffi")
    license_entry["license"] = license_expression
    if source is not None:
        license_entry["source"] = source
    _write_json(license_path, licenses)
    _rehash_license_evidence(repository, "core")


def _assert_rejected(
    report: ProvenanceVerificationReport,
    *error_fragments: str,
) -> None:
    assert report.valid is False
    assert any(
        all(fragment.lower() in error.lower() for fragment in error_fragments)
        for error in report.errors
    ), report.errors


def test_import_smoke_uses_and_proves_the_profile_python_patch() -> None:
    workflow = (_ROOT / ".github/workflows/provenance.yml").read_text(encoding="utf-8")
    marker = "\n  import-smoke:\n"
    assert workflow.count(marker) == 1
    job = workflow.split(marker, maxsplit=1)[1]

    assert '"${profile_root}/profile.json"' in job
    assert "json.load(" in job
    assert '["python_version"]' in job
    assert 'uv python install "${python_version}"' in job
    assert 'uv python pin --directory "${profile_root}" "${python_version}"' in job

    sync_commands = [line for line in job.splitlines() if "uv sync" in line]
    assert len(sync_commands) == 2
    assert all('--python "${python_version}"' in line for line in sync_commands)
    assert "platform.python_version()" in job
    assert 'test "${actual_python_version}" = "${python_version}"' in job

    load_index = job.index('"${profile_root}/profile.json"')
    install_index = job.index('uv python install "${python_version}"')
    pin_index = job.index('uv python pin --directory "${profile_root}"')
    sync_index = min(job.index(command.strip()) for command in sync_commands)
    proof_index = job.index('test "${actual_python_version}" = "${python_version}"')
    assert load_index < install_index < pin_index < sync_index < proof_index


@pytest.mark.parametrize(
    "license_expression",
    ("NONE", "MIT OR"),
    ids=("spdx-none-sentinel", "malformed-spdx-expression"),
)
def test_invalid_spdx_license_expression_is_rejected_after_synchronized_rehash(
    repository_copy: Path,
    license_expression: str,
) -> None:
    _replace_license_evidence(
        repository_copy,
        license_expression=license_expression,
    )

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "invalid SPDX license expression")


def test_self_consistent_transitive_license_forgery_has_a_trusted_content_anchor(
    repository_copy: Path,
) -> None:
    _replace_license_evidence(
        repository_copy,
        license_expression="Apache-2.0",
        source="https://pypi.org/pypi/cffi/2.1.1/json#info.summary",
    )

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "license", "central frozen policy")
    assert not any(
        fragment in error
        for error in report.errors
        for fragment in (
            "hash mismatch",
            "SBOM license mismatch",
            "license manifest does not cover exact lock",
        )
    ), report.errors
