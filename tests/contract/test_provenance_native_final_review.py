from __future__ import annotations

import io
import json
import shutil
import stat
import tarfile
import zipfile
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


def _remove_toml_array_assignment(path: Path, key: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.lstrip().startswith(f"{key} =")
        ),
        None,
    )
    if start is None:
        return

    value = lines[start].split("=", maxsplit=1)[1]
    balance = value.count("[") - value.count("]")
    end = start + 1
    while balance > 0 and end < len(lines):
        balance += lines[end].count("[") - lines[end].count("]")
        end += 1
    assert balance == 0
    path.write_text("".join((*lines[:start], *lines[end:])), encoding="utf-8")


def _assert_rejected(
    report: ProvenanceVerificationReport,
    *error_fragments: str,
) -> None:
    assert report.valid is False
    assert any(
        all(fragment.lower() in error.lower() for fragment in error_fragments)
        for error in report.errors
    ), report.errors


@pytest.mark.parametrize(
    ("profile_id", "source_builds"),
    (
        ("authoring", ("google-search-results",)),
        ("env-citylearn", ("tinynumpy",)),
        ("env-metadrive", ("progressbar", "scenarionet")),
        ("env-smacv2", ("mpyq", "s2protocol", "smacv2")),
    ),
)
def test_target_source_builds_require_frozen_backend_constraints(
    repository_copy: Path,
    profile_id: str,
    source_builds: tuple[str, ...],
) -> None:
    pyproject_path = repository_copy / "profiles" / profile_id / "pyproject.toml"
    _remove_toml_array_assignment(pyproject_path, "build-constraint-dependencies")
    _rehash_profile(repository_copy, profile_id)

    report = verify_provenance(repository_copy)

    for package_name in source_builds:
        _assert_rejected(report, profile_id, "build", package_name)


@pytest.mark.parametrize(
    "relative_path",
    (
        "profiles/core/agent2world.py",
        "profiles/core/agent2world.tar.gz",
        "profiles/replication-agent2world-restricted/agent2world.py",
    ),
)
def test_unregistered_restricted_payload_is_rejected_from_profile_directories(
    repository_copy: Path,
    relative_path: str,
) -> None:
    ingress = repository_copy / relative_path
    ingress.write_bytes(b"unregistered restricted upstream payload\n")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "restricted", relative_path)


@pytest.mark.parametrize(
    "relative_path",
    (
        pytest.param("profiles/core/.cache", id="root-cache"),
        pytest.param("profiles/core/.venv", id="root-venv"),
        pytest.param("profiles/core/nested/.cache", id="nested-cache"),
        pytest.param("profiles/core/nested/.venv", id="nested-venv"),
    ),
)
def test_ignored_profile_directory_symlinks_are_rejected_at_any_depth(
    repository_copy: Path,
    relative_path: str,
) -> None:
    target = repository_copy.parent / "symlink-target"
    target.mkdir()
    ingress = repository_copy / relative_path
    ingress.parent.mkdir(parents=True, exist_ok=True)
    ingress.symlink_to(target, target_is_directory=True)

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "symlink", relative_path)


def _write_renamed_archive(path: Path, member_name: str, payload: bytes) -> None:
    if path.name.endswith(".tar.gz"):
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        with tarfile.open(path, mode="w:gz") as archive:
            archive.addfile(member, io.BytesIO(payload))
        return
    if path.suffix == ".zip":
        with zipfile.ZipFile(
            path, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(member_name, payload)
        return
    raise AssertionError(f"unsupported test archive: {path}")


@pytest.mark.parametrize(
    ("archive_name", "member_name", "payload"),
    (
        pytest.param(
            "payload.tar.gz",
            "vendor/agent2world/source.py",
            b"benign payload\n",
            id="tar-gz-member-path",
        ),
        pytest.param(
            "payload.zip",
            "vendor/source.py",
            b"restricted agent2world payload\n",
            id="zip-member-content",
        ),
    ),
)
def test_renamed_archives_with_restricted_members_are_rejected(
    repository_copy: Path,
    archive_name: str,
    member_name: str,
    payload: bytes,
) -> None:
    ingress = repository_copy / "profiles" / "core" / archive_name
    _write_renamed_archive(ingress, member_name, payload)
    assert b"agent2world" not in ingress.read_bytes()

    report = verify_provenance(repository_copy)

    _assert_rejected(
        report, "restricted", ingress.relative_to(repository_copy).as_posix()
    )


def test_unregistered_benign_profile_file_stays_outside_the_build_context(
    repository_copy: Path,
) -> None:
    benign = repository_copy / "profiles" / "core" / "operator-notes.txt"
    benign.write_text("local operator notes\n", encoding="utf-8")

    report = verify_provenance(repository_copy)

    assert report.valid is True, report.errors


def test_core_smoke_contract_forbids_oasis() -> None:
    smoke = _read_json_object(_REPOSITORY_ROOT / "profiles" / "core" / "smoke.json")

    forbidden_imports = smoke["forbidden_imports"]
    assert type(forbidden_imports) is list
    assert "oasis" in forbidden_imports


def test_core_central_policy_rejects_smoke_without_oasis(
    repository_copy: Path,
) -> None:
    smoke_path = repository_copy / "profiles" / "core" / "smoke.json"
    smoke = _read_json_object(smoke_path)
    forbidden_imports = smoke["forbidden_imports"]
    assert type(forbidden_imports) is list
    smoke["forbidden_imports"] = [name for name in forbidden_imports if name != "oasis"]
    _write_json(smoke_path, smoke)
    _rehash_profile(repository_copy, "core")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "smoke", "central policy")
