from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter, ValidationError

from automarkov.security import provenance
from automarkov.security.provenance import (
    ProvenanceVerificationReport,
    UpstreamManifest,
    verify_provenance,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_TEXT2WORLD_REPOSITORY = "https://github.com/Aaron617/text2world"
_TEXT2WORLD_COMMIT = "9440ff7732fca4bcc8d9fb59a435886735f4059a"
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
    for relative_path in ("profiles", "references", "src"):
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


def _assert_structured_rejection(
    report: ProvenanceVerificationReport,
    *fragments: str,
) -> None:
    assert report.valid is False
    assert any(
        all(fragment.lower() in error.lower() for fragment in fragments)
        for error in report.errors
    ), report.errors


def _initialize_git_index(repository: Path) -> None:
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True)
    subprocess.run(("git", "add", "--all"), cwd=repository, check=True)


def _upstream_payload(resource_id: str) -> dict[str, object]:
    catalog = json.loads(
        (_REPOSITORY_ROOT / "references" / "manifest.yaml").read_bytes()
    )
    assert type(catalog) is list
    payload = next(item for item in catalog if item["resource_id"] == resource_id)
    assert type(payload) is dict
    return cast(dict[str, object], payload)


def test_registered_text2world_markers_fail_closed_in_publishable_source(
    repository_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_path = "src/automarkov/cli.py"
    source_path = repository_copy / relative_path
    original = source_path.read_bytes()
    markers = (
        _TEXT2WORLD_REPOSITORY.encode("ascii"),
        _TEXT2WORLD_COMMIT.encode("ascii"),
        b"https://github.com/aArOn617/TeXt2WoRlD",
        base64.b64encode(_TEXT2WORLD_REPOSITORY.encode("ascii")),
    )

    for marker in markers:
        payload = original + b"\n# " + marker + b"\n"
        source_path.write_bytes(payload)
        monkeypatch.setitem(
            provenance._REGISTERED_SOURCE_HASHES,
            relative_path,
            "sha256:" + sha256(payload).hexdigest(),
        )

        report = verify_provenance(repository_copy)

        _assert_structured_rejection(report, "text2world", relative_path)
        source_path.write_bytes(original)


def test_approved_git_source_requires_matching_full_commit_rev_and_fragment() -> None:
    repository = "https://github.com/example/project"
    commit = "0123456789abcdef0123456789abcdef01234567"
    valid = f"{repository}?rev={commit}#{commit}"
    invalid_sources = (
        repository,
        f"{repository}?branch=main#{commit}",
        f"{repository}?tag=v1.0.0#{commit}",
        f"{repository}?rev={commit[:12]}#{commit[:12]}",
        f"{repository}?rev={commit}#{'f' * 40}",
        f"{repository}?rev={commit}&depth=1#{commit}",
        f"https://github.com:443/example/project?rev={commit}#{commit}",
    )

    assert provenance._require_approved_git_source(valid) == valid
    for source in invalid_sources:
        with pytest.raises(ValueError):
            provenance._require_approved_git_source(source)


def test_build_context_mode_uses_only_the_owner_execute_bit(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("frozen input\n", encoding="utf-8")
    source.chmod(0o644)
    regular_hash = provenance._build_context_hash(tmp_path, (source.name,))

    source.chmod(0o654)
    group_execute_hash = provenance._build_context_hash(tmp_path, (source.name,))
    source.chmod(0o755)
    owner_execute_hash = provenance._build_context_hash(tmp_path, (source.name,))

    assert group_execute_hash == regular_hash
    assert owner_execute_hash != regular_hash


def test_exact_versions_reject_free_text_and_accept_canonical_local_version() -> None:
    adapter = TypeAdapter(provenance.ExactVersion)
    upstream = _upstream_payload("tavily-python")
    canonical_version = "0.25.1+cu129"

    assert adapter.validate_python(canonical_version, strict=True) == canonical_version
    assert (
        UpstreamManifest.model_validate(
            {**upstream, "release": canonical_version},
            strict=True,
        ).release
        == canonical_version
    )
    for invalid_version in ("1.0latest", "unknown", "unresolved"):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid_version, strict=True)
        with pytest.raises(ValidationError):
            UpstreamManifest.model_validate(
                {**upstream, "release": invalid_version},
                strict=True,
            )


def test_non_utf8_filename_returns_a_structured_invalid_report(
    repository_copy: Path,
) -> None:
    raw_path = os.fsencode(repository_copy) + b"/invalid-\xff.py"
    descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(descriptor, b"ordinary source\n")
    finally:
        os.close(descriptor)

    report = verify_provenance(repository_copy)

    _assert_structured_rejection(report, "utf-8", "filename")


def test_restricted_blob_deleted_from_worktree_is_rejected_from_git_index(
    repository_copy: Path,
) -> None:
    restricted_path = repository_copy / "src" / "restricted_copy.py"
    restricted_path.write_text(
        f"# {_TEXT2WORLD_REPOSITORY}\n",
        encoding="utf-8",
    )
    _initialize_git_index(repository_copy)
    restricted_path.unlink()

    report = verify_provenance(repository_copy)

    _assert_structured_rejection(
        report, "git index", "text2world", "restricted_copy.py"
    )


def test_staged_restricted_blob_is_rejected_when_worktree_was_restored(
    repository_copy: Path,
) -> None:
    source_path = repository_copy / "src" / "automarkov" / "cli.py"
    original = source_path.read_bytes()
    _initialize_git_index(repository_copy)
    source_path.write_bytes(
        original + b"\n# " + _TEXT2WORLD_REPOSITORY.encode("ascii") + b"\n"
    )
    subprocess.run(
        ("git", "add", "--", "src/automarkov/cli.py"), cwd=repository_copy, check=True
    )
    source_path.write_bytes(original)

    report = verify_provenance(repository_copy)

    _assert_structured_rejection(
        report, "git index", "text2world", "src/automarkov/cli.py"
    )
