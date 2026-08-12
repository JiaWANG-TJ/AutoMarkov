from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from automarkov import provenance
from automarkov.provenance import ProvenanceVerificationReport, verify_provenance

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


def _assert_rejected(
    report: ProvenanceVerificationReport,
    relative_path: str,
) -> None:
    assert report.valid is False
    assert any(relative_path.lower() in error.lower() for error in report.errors), (
        report.errors
    )


def test_linked_worktree_git_control_file_is_not_publish_payload(
    repository_copy: Path,
    tmp_path: Path,
) -> None:
    def run_git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repository_copy), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    run_git("init", "--quiet")
    run_git("add", "--all")
    run_git(
        "-c",
        "user.name=AutoMarkov Tests",
        "-c",
        "user.email=tests@automarkov.invalid",
        "commit",
        "--quiet",
        "-m",
        "linked worktree fixture",
    )
    linked_worktree = tmp_path / "linked-worktree"
    run_git("worktree", "add", "--detach", "--quiet", str(linked_worktree), "HEAD")

    git_control = linked_worktree / ".git"
    control_payload = git_control.read_text(encoding="utf-8")
    assert git_control.is_file()
    assert control_payload.startswith("gitdir: ")
    assert Path(control_payload.removeprefix("gitdir: ").strip()).is_absolute()

    report = verify_provenance(linked_worktree)

    assert report.valid is True, report.errors


def test_restricted_source_is_rejected_from_root_publish_tree(
    repository_copy: Path,
) -> None:
    relative_path = "vendor/agent2world.py"
    ingress = repository_copy / relative_path
    ingress.parent.mkdir()
    ingress.write_text("restricted upstream source\n", encoding="utf-8")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, relative_path)


def test_root_only_ignore_name_cannot_hide_restricted_source_in_package(
    repository_copy: Path,
) -> None:
    relative_path = "src/automarkov/external/restricted_ingress.py"
    ingress = repository_copy / relative_path
    ingress.parent.mkdir(parents=True)
    ingress.write_text("agent2world restricted upstream source\n", encoding="utf-8")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, relative_path)


def test_public_benchmark_cannot_hide_restricted_source(
    repository_copy: Path,
) -> None:
    relative_path = "benchmarks/core/restricted_ingress.py"
    ingress = repository_copy / relative_path
    ingress.parent.mkdir(parents=True)
    ingress.write_text("agent2world restricted upstream source\n", encoding="utf-8")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, relative_path)


@pytest.mark.parametrize("boundary", ("gold", "sealed"))
def test_benchmark_sealed_boundaries_are_not_read(
    repository_copy: Path,
    boundary: str,
) -> None:
    hidden = repository_copy / "benchmarks" / "suite" / boundary / "answer.bin"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"agent2world sealed answer\x00\xff")

    report = verify_provenance(repository_copy)

    assert report.valid is True, report.errors


@pytest.mark.parametrize("cache_root", (".cache", ".swanlab"))
def test_runtime_cache_boundaries_are_not_read(
    repository_copy: Path,
    cache_root: str,
) -> None:
    hidden = repository_copy / cache_root / "private.bin"
    hidden.parent.mkdir()
    hidden.write_bytes(b"agent2world private runtime cache\x00\xff")

    report = verify_provenance(repository_copy)

    assert report.valid is True, report.errors


def test_sensitive_publish_tree_names_fail_closed_while_ignored_roots_are_not_read(
    repository_copy: Path,
) -> None:
    ignored_paths = (
        "secrets/service/token.bin",
        "private/research/input.bin",
        "external/restricted/source.bin",
        "artifacts/run/output.bin",
        "benchmarks/suite/nested/gold/answer.bin",
        "benchmarks/suite/nested/sealed/answer.bin",
        ".cache/runtime/payload.bin",
        ".swanlab/runtime/payload.bin",
    )
    for relative_path in ignored_paths:
        hidden = repository_copy / relative_path
        hidden.parent.mkdir(parents=True, exist_ok=True)
        hidden.write_bytes(b"agent2world ignored boundary\x00\xff")

    env_example = repository_copy / ".env.example"
    env_example.write_text("TOKEN=replace-me\n", encoding="utf-8")

    baseline = verify_provenance(repository_copy)

    assert baseline.valid is True, baseline.errors

    env_example.write_text("UPSTREAM=agent2world\n", encoding="utf-8")

    restricted_example = verify_provenance(repository_copy)

    _assert_rejected(restricted_example, ".env.example")
    assert not any(
        relative_path in error
        for error in restricted_example.errors
        for relative_path in ignored_paths
    ), restricted_example.errors

    env_example.write_text("TOKEN=replace-me\n", encoding="utf-8")
    sensitive_paths = (
        "src/evil.key",
        "config/release.pem",
        "logs/release.log",
        "config/.env.production",
    )
    for relative_path in sensitive_paths:
        sensitive = repository_copy / relative_path
        sensitive.parent.mkdir(parents=True, exist_ok=True)
        sensitive.write_text("benign placeholder\n", encoding="utf-8")

    report = verify_provenance(repository_copy)
    lowered_errors = tuple(error.lower() for error in report.errors)
    missing_paths = tuple(
        relative_path
        for relative_path in sensitive_paths
        if not any(relative_path.lower() in error for error in lowered_errors)
    )

    assert report.valid is False
    assert missing_paths == (), report.errors
    assert not any(
        relative_path in error
        for error in report.errors
        for relative_path in ignored_paths
    ), report.errors


def test_git_publish_inventory_rejects_tracked_sensitive_paths_without_reading_them(
    repository_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repository_copy), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    run_git("init", "--quiet")
    run_git("add", "--all")

    baseline = verify_provenance(repository_copy)

    assert baseline.valid is True, baseline.errors

    tracked_existing_paths = (
        "src/evil.key",
        "config/release.pem",
        "logs/release.log",
    )
    tracked_pruned_path = "secrets/publish.key"
    tracked_missing_path = "config/.env.production"
    tracked_sensitive_paths = (
        *tracked_existing_paths,
        tracked_pruned_path,
        tracked_missing_path,
    )
    for relative_path in tracked_sensitive_paths:
        sensitive = repository_copy / relative_path
        sensitive.parent.mkdir(parents=True, exist_ok=True)
        sensitive.write_text("benign placeholder\n", encoding="utf-8")
    run_git("add", "--force", "--", *tracked_sensitive_paths)
    (repository_copy / tracked_missing_path).unlink()

    untracked_root_env = ".env"
    untracked_ignored_paths = (
        "secrets/private.bin",
        "private/research/input.bin",
        "external/restricted/source.bin",
        "artifacts/run/output.bin",
        "benchmarks/suite/nested/gold/answer.bin",
        "benchmarks/suite/nested/sealed/answer.bin",
        ".cache/runtime/payload.bin",
        ".swanlab/runtime/payload.bin",
    )
    for relative_path in (untracked_root_env, *untracked_ignored_paths):
        ignored = repository_copy / relative_path
        ignored.parent.mkdir(parents=True, exist_ok=True)
        ignored.write_bytes(b"agent2world ignored boundary\x00\xff")

    tracked_inventory = frozenset(run_git("ls-files").splitlines())
    assert set(tracked_sensitive_paths) <= tracked_inventory
    assert untracked_root_env not in tracked_inventory
    assert not (set(untracked_ignored_paths) & tracked_inventory)

    protected_paths = frozenset(
        (*tracked_sensitive_paths, untracked_root_env, *untracked_ignored_paths)
    )
    protected_read_attempts: list[str] = []
    read_regular_file = provenance._read_regular_file

    def reject_protected_read(path: Path) -> bytes:
        try:
            relative_path = path.relative_to(repository_copy).as_posix()
        except ValueError:
            return read_regular_file(path)
        if relative_path in protected_paths:
            protected_read_attempts.append(relative_path)
            pytest.fail(f"verifier read protected path: {relative_path}")
        return read_regular_file(path)

    monkeypatch.setattr(provenance, "_read_regular_file", reject_protected_read)

    report = verify_provenance(repository_copy)
    lowered_errors = tuple(error.lower() for error in report.errors)
    missing_rejections = tuple(
        relative_path
        for relative_path in tracked_sensitive_paths
        if not any(relative_path.lower() in error for error in lowered_errors)
    )
    reported_tokens = {
        token.strip(".,:;()[]{}'\"").lower()
        for error in report.errors
        for token in error.split()
    }

    assert missing_rejections == (), report.errors
    assert report.valid is False
    assert protected_read_attempts == []
    assert untracked_root_env not in reported_tokens
    assert not any(
        relative_path in error
        for error in report.errors
        for relative_path in untracked_ignored_paths
    ), report.errors


def test_git_publish_inventory_rejects_tracked_ignored_roots_by_path_only(
    repository_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repository_copy), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    shutil.copy2(_REPOSITORY_ROOT / ".gitignore", repository_copy / ".gitignore")
    sentinel_paths = (
        "artifacts/.gitignore",
        "external/.gitignore",
    )
    for relative_path in sentinel_paths:
        sentinel = repository_copy / relative_path
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("*\n!.gitignore\n", encoding="utf-8")

    tracked_forbidden_paths = (
        "external/restricted/source.py",
        "secrets/payload.txt",
        "private/payload.py",
        "references/checkouts/vendor.py",
        "artifacts/output.txt",
        "benchmarks/suite/gold/answer.py",
        "benchmarks/suite/sealed/answer.py",
        "checkpoints/policy.pt",
    )
    untracked_forbidden_paths = (
        "external/restricted/untracked.py",
        "secrets/untracked.txt",
        "private/untracked.py",
        "references/checkouts/untracked.py",
        "artifacts/untracked.txt",
        "benchmarks/suite/gold/untracked.py",
        "benchmarks/suite/sealed/untracked.py",
        "checkpoints/untracked.pt",
    )
    for relative_path in (*tracked_forbidden_paths, *untracked_forbidden_paths):
        protected = repository_copy / relative_path
        protected.parent.mkdir(parents=True, exist_ok=True)
        protected.write_bytes(b"content must remain unread\x00\xff")

    run_git("init", "--quiet")
    run_git("add", "--all")
    baseline_inventory = frozenset(run_git("ls-files").splitlines())
    assert set(sentinel_paths) <= baseline_inventory
    assert not (set(tracked_forbidden_paths) & baseline_inventory)
    assert not (set(untracked_forbidden_paths) & baseline_inventory)

    protected_paths = frozenset(
        (*sentinel_paths, *tracked_forbidden_paths, *untracked_forbidden_paths)
    )
    protected_read_attempts: list[str] = []
    read_regular_file = provenance._read_regular_file

    def reject_protected_read(path: Path) -> bytes:
        try:
            relative_path = path.relative_to(repository_copy).as_posix()
        except ValueError:
            return read_regular_file(path)
        if relative_path in protected_paths:
            protected_read_attempts.append(relative_path)
            pytest.fail(f"verifier read protected path: {relative_path}")
        return read_regular_file(path)

    monkeypatch.setattr(provenance, "_read_regular_file", reject_protected_read)

    baseline = verify_provenance(repository_copy)

    assert baseline.valid is True, baseline.errors
    assert protected_read_attempts == []

    for relative_path in tracked_forbidden_paths:
        run_git("add", "--force", "--", relative_path)
        tracked_inventory = frozenset(run_git("ls-files").splitlines())
        assert relative_path in tracked_inventory

        report = verify_provenance(repository_copy)

        assert report.valid is False
        assert len(report.errors) == 1, report.errors
        assert relative_path.lower() in report.errors[0].lower(), report.errors
        assert "tracked for publication" in report.errors[0].lower(), report.errors
        assert protected_read_attempts == []
        assert not any(
            untracked_path in error
            for error in report.errors
            for untracked_path in untracked_forbidden_paths
        ), report.errors
        assert not any(
            sentinel_path in error
            for error in report.errors
            for sentinel_path in sentinel_paths
        ), report.errors

        run_git("rm", "--quiet", "--cached", "--force", "--", relative_path)
        untracked_again = verify_provenance(repository_copy)

        assert untracked_again.valid is True, untracked_again.errors
        assert protected_read_attempts == []


@pytest.mark.parametrize(
    "relative_path",
    (
        "src/automarkov/.venv/restricted.py",
        "tests/contract/fixtures/.cache/restricted.bin",
    ),
)
def test_git_publish_inventory_rejects_tracked_ignored_components_without_reading(
    repository_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    def run_git(*arguments: str) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repository_copy), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout

    run_git("init", "--quiet")
    run_git("add", "--all")
    baseline = verify_provenance(repository_copy)
    assert baseline.valid is True, baseline.errors

    protected = repository_copy / relative_path
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"content must remain unread\x00\xff")
    run_git("add", "--force", "--", relative_path)
    assert relative_path in frozenset(run_git("ls-files").splitlines())

    protected_read_attempts: list[str] = []
    read_regular_file = provenance._read_regular_file

    def reject_protected_read(path: Path) -> bytes:
        if path == protected:
            protected_read_attempts.append(relative_path)
            pytest.fail(f"verifier read protected path: {relative_path}")
        return read_regular_file(path)

    monkeypatch.setattr(provenance, "_read_regular_file", reject_protected_read)

    report = verify_provenance(repository_copy)

    assert protected_read_attempts == []
    _assert_rejected(report, relative_path)
    assert any("tracked for publication" in error.lower() for error in report.errors), (
        report.errors
    )


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param(b"\x04\x22\x4d\x18\x60\x40\x82\x00", id="lz4-magic"),
        pytest.param(
            b"YWdlbnQyd29ybGQ=",
            id="base64-restricted-marker",
        ),
        pytest.param(
            b"\x00\xff\x81\x10opaque\x00payload\xfe",
            id="arbitrary-opaque-binary",
        ),
    ),
)
def test_unregistered_profile_payload_fails_closed(
    repository_copy: Path,
    payload: bytes,
) -> None:
    relative_path = "profiles/core/payload.dat"
    ingress = repository_copy / relative_path
    ingress.write_bytes(payload)
    assert b"agent2world" not in payload.lower()

    report = verify_provenance(repository_copy)

    _assert_rejected(report, relative_path)


def test_combined_base64_restricted_payload_is_rejected(
    repository_copy: Path,
) -> None:
    relative_path = "src/automarkov/combined-encoded-payload.txt"
    ingress = repository_copy / relative_path
    ingress.parent.mkdir(parents=True)
    restricted_marker = b"agent2" + b"world"
    payload = base64.b64encode(
        b"x" + restricted_marker + b" restricted upstream source"
    )
    assert restricted_marker not in payload.lower()
    assert base64.b64encode(restricted_marker).lower() not in payload.lower()
    ingress.write_bytes(payload)

    report = verify_provenance(repository_copy)

    _assert_rejected(report, relative_path)


def test_plain_text_operator_notes_remain_allowed(repository_copy: Path) -> None:
    relative_path = "profiles/core/operator-notes.txt"
    notes = repository_copy / relative_path
    notes.write_text(
        "Local operator notes for the frozen profile recipe.\n",
        encoding="utf-8",
    )

    report = verify_provenance(repository_copy)

    assert report.valid is True, report.errors


def test_declaration_allowlist_rejects_appended_executable_restricted_source(
    repository_copy: Path,
) -> None:
    relative_path = "tests/contract/test_runtime_profiles.py"
    declaration = repository_copy / relative_path
    declaration.parent.mkdir(parents=True)
    shutil.copy2(_REPOSITORY_ROOT / relative_path, declaration)

    baseline = verify_provenance(repository_copy)
    assert baseline.valid is True, baseline.errors

    restricted_name = "agent2" + "world"
    declaration.write_text(
        declaration.read_text(encoding="utf-8")
        + "\n\ndef _vendored_restricted_runtime() -> bytes:\n"
        + f'    return b"{restricted_name} restricted upstream source"\n',
        encoding="utf-8",
    )
    compile(declaration.read_text(encoding="utf-8"), str(declaration), "exec")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, relative_path)
