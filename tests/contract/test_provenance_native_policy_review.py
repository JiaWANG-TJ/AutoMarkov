from __future__ import annotations

import json
import shutil
import stat
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from automarkov.domain.canonical import canonical_json_bytes
from automarkov.security.provenance import ProvenanceVerificationReport, verify_provenance

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_COPY_IGNORE = shutil.ignore_patterns(
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
)
_RESTRICTED_MARKER = "agent2world"


@pytest.fixture
def repository_copy(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative_path in ("profiles", "references", "src", ".github"):
        source = _REPOSITORY_ROOT / relative_path
        if source.exists():
            shutil.copytree(
                source,
                repository / relative_path,
                ignore=_COPY_IGNORE,
            )
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_REPOSITORY_ROOT / filename, repository / filename)

    baseline = verify_provenance(repository)
    assert baseline.valid is True, baseline.errors
    return repository


def _assert_rejected(
    report: ProvenanceVerificationReport,
    *error_fragments: str,
) -> None:
    assert report.valid is False
    lowered_errors = tuple(error.lower() for error in report.errors)
    assert any(
        all(fragment.lower() in error for fragment in error_fragments)
        for error in lowered_errors
    ), report.errors


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


def _rehash_profile(repository: Path, profile_id: str) -> None:
    profile_root = repository / "profiles" / profile_id
    profile_path = profile_root / "profile.json"
    profile = _read_json_object(profile_path)
    for field, path_field in (
        ("lock_hash", "lockfile_path"),
        ("sbom_hash", "sbom_path"),
        ("license_manifest_hash", "license_manifest_path"),
        ("smoke_contract_hash", "smoke_contract_path"),
    ):
        relative_path = profile[path_field]
        assert type(relative_path) is str
        profile[field] = _file_hash(profile_root / relative_path)

    build_context_files = profile["build_context_files"]
    assert type(build_context_files) is list
    assert all(type(relative_path) is str for relative_path in build_context_files)
    build_context = {
        "domain": "AutoMarkov-Runtime-Profile-Build-Context-v2",
        "files": [
            {
                "mode": format(
                    stat.S_IMODE(
                        (profile_root / cast(str, relative_path)).stat().st_mode
                    ),
                    "04o",
                ),
                "path": relative_path,
                "sha256": _file_hash(profile_root / cast(str, relative_path)),
            }
            for relative_path in build_context_files
        ],
    }
    profile["build_context_hash"] = (
        "sha256:" + sha256(canonical_json_bytes(build_context)).hexdigest()
    )
    _write_json(profile_path, profile)


def test_restricted_marker_scan_covers_all_publishable_source_extensions_but_not_caches(
    repository_copy: Path,
) -> None:
    source_root = repository_copy / "src"
    ignored_paths = (
        source_root / ".venv" / "ignored_ingress.py",
        source_root / ".cache" / "ignored_ingress.toml",
    )
    for ignored_path in ignored_paths:
        ignored_path.parent.mkdir(parents=True, exist_ok=True)
        ignored_path.write_text(_RESTRICTED_MARKER, encoding="utf-8")

    ingress = source_root / "automarkov" / "restricted_ingress.payload"
    ingress.write_text(_RESTRICTED_MARKER, encoding="utf-8")

    report = verify_provenance(repository_copy)

    _assert_rejected(
        report, _RESTRICTED_MARKER, ingress.relative_to(repository_copy).as_posix()
    )
    assert not any(
        ignored_path.relative_to(repository_copy).as_posix() in error
        for error in report.errors
        for ignored_path in ignored_paths
    ), report.errors


@pytest.mark.parametrize(
    "parser_directive",
    ("# syntax=docker/dockerfile:1.7", "# escape=`"),
)
def test_containerfile_rejects_unfrozen_parser_directives_after_context_rehash(
    repository_copy: Path,
    parser_directive: str,
) -> None:
    containerfile_path = repository_copy / "profiles" / "core" / "Containerfile"
    containerfile = containerfile_path.read_text(encoding="utf-8")
    containerfile_path.write_text(
        f"{parser_directive}\n{containerfile}",
        encoding="utf-8",
    )
    _rehash_profile(repository_copy, "core")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "containerfile", "directive")
    assert not any("build context hash mismatch" in error for error in report.errors), (
        report.errors
    )


@pytest.mark.parametrize(
    "capabilities",
    (
        ["authoring.compiler.v1", "evidence.search.v1"],
        ["authoring.compilor.v1"],
    ),
)
def test_authoring_capabilities_must_match_the_exact_central_profile_tuple(
    repository_copy: Path,
    capabilities: list[str],
) -> None:
    profile_path = repository_copy / "profiles" / "authoring" / "profile.json"
    profile = _read_json_object(profile_path)
    profile["capabilities"] = capabilities
    _write_json(profile_path, profile)

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "authoring", "capabil")
