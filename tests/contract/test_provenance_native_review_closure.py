from __future__ import annotations

import io
import json
import shutil
import stat
import tarfile
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from automarkov.domain.canonical import canonical_json_bytes
from automarkov.security import provenance
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


@pytest.fixture
def repository_copy(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative_path in ("profiles", "references"):
        shutil.copytree(
            _REPOSITORY_ROOT / relative_path,
            repository / relative_path,
            ignore=_COPY_IGNORE,
        )
    for filename in ("pyproject.toml", "uv.lock"):
        shutil.copy2(_REPOSITORY_ROOT / filename, repository / filename)

    baseline = verify_provenance(repository)
    assert baseline.valid is True, baseline.errors
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


def _assert_rejected(
    report: ProvenanceVerificationReport,
    *error_fragments: str,
) -> None:
    assert report.valid is False
    assert any(
        all(fragment.lower() in error.lower() for fragment in error_fragments)
        for error in report.errors
    ), report.errors


def test_magic_detected_archive_cannot_hide_restricted_member_path(
    repository_copy: Path,
) -> None:
    archive_bytes = io.BytesIO()
    payload = b"benign source payload\n"
    member = tarfile.TarInfo("vendor/agent2world/source.py")
    member.size = len(payload)
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
        archive.addfile(member, io.BytesIO(payload))

    ingress = repository_copy / "profiles" / "core" / "payload.dat"
    ingress.write_bytes(archive_bytes.getvalue())
    assert ingress.read_bytes().startswith(b"\x1f\x8b")
    assert b"agent2world" not in ingress.read_bytes().lower()

    report = verify_provenance(repository_copy)

    _assert_rejected(
        report,
        "restricted",
        ingress.relative_to(repository_copy).as_posix(),
    )


def test_malformed_lock_shape_returns_structured_invalid_report(
    repository_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = repository_copy / "profiles" / "core" / "uv.lock"
    lock_path.write_text("package = 1\n", encoding="utf-8")
    _rehash_profile(repository_copy, "core")

    core = next(
        profile
        for profile in provenance.load_runtime_profiles(repository_copy / "profiles")
        if profile.profile_id == "core"
    )
    expected_hashes = provenance._EXPECTED_PROFILE_MANIFEST_HASHES
    assert type(expected_hashes) is dict
    monkeypatch.setitem(expected_hashes, "core", core.manifest_hash)
    core_policy = provenance._PROFILE_POLICIES["core"]
    monkeypatch.setitem(
        provenance._PROFILE_POLICIES,
        "core",
        core_policy._replace(lock_hash=core.lock_hash),
    )

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "invalid or empty uv lock")
    assert not any(
        fragment in error.lower()
        for error in report.errors
        for fragment in (
            "hash mismatch",
            "central frozen policy",
            "central frozen profile identity mismatch",
        )
    ), report.errors


@pytest.mark.parametrize(
    "marker",
    (
        pytest.param("extras == 'foo'", id="extras"),
        pytest.param("dependency_groups == 'x'", id="dependency-groups"),
    ),
)
def test_unfrozen_lock_marker_returns_structured_invalid_report(
    repository_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    lock_path = repository_copy / "profiles" / "core" / "uv.lock"
    lock = lock_path.read_text(encoding="utf-8")
    dependency = '{ name = "cryptography" },'
    assert lock.count(dependency) == 1
    lock_path.write_text(
        lock.replace(
            dependency,
            f'{{ name = "cryptography", marker = "{marker}" }},',
        ),
        encoding="utf-8",
    )
    _rehash_profile(repository_copy, "core")

    core = next(
        profile
        for profile in provenance.load_runtime_profiles(repository_copy / "profiles")
        if profile.profile_id == "core"
    )
    expected_hashes = provenance._EXPECTED_PROFILE_MANIFEST_HASHES
    assert type(expected_hashes) is dict
    monkeypatch.setitem(expected_hashes, "core", core.manifest_hash)
    core_policy = provenance._PROFILE_POLICIES["core"]
    monkeypatch.setitem(
        provenance._PROFILE_POLICIES,
        "core",
        core_policy._replace(lock_hash=core.lock_hash),
    )

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "uv lock marker is not frozen")
