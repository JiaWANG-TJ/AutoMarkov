from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOT = "20250910T000000Z"
_GIT_VERSION = "1:2.39.5-0+deb12u2"
_CA_CERTIFICATES_VERSION = "20230311+deb12u1"
_BOOKWORM_BASES = {
    "3.10.18": (
        "python:3.10.18-slim-bookworm@"
        "sha256:b4d66d07136c546f1765eae2bfcce9a64fa95f37c717c02bedd06d0476d1dbbd"
    ),
    "3.11.13": (
        "python:3.11.13-slim-bookworm@"
        "sha256:cec9aa7aa96eea4fa036e9b82be1e6b325f2e3707f462d885868df51ec0a4b47"
    ),
    "3.12.11": (
        "python:3.12.11-slim-bookworm@"
        "sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49"
    ),
}
_GIT_PROFILES = {
    "env-metadrive": (
        "https://github.com/metadriverse/scenarionet",
        "d4acdb5f5a844744fc85cb2dc3880d7d4a6eb170",
    ),
    "env-smacv2": (
        "https://github.com/oxwhirl/smacv2",
        "577ab5a2cff2391f8df582da5731ea9cd6adf3c6",
    ),
}
_SOURCE_BUILD_PROFILES = {
    "authoring": ("google-search-results",),
    "env-citylearn": ("tinynumpy",),
    "env-metadrive": ("progressbar", "scenarionet"),
    "env-smacv2": ("mpyq", "s2protocol", "smacv2"),
}
_SETUPTOOLS_ASSERTION = (
    "test \"$(.venv/bin/python -c 'import importlib.metadata; "
    'print(importlib.metadata.version("setuptools"))\')" = "84.0.0"'
)


def test_all_profile_recipes_use_the_frozen_bookworm_base() -> None:
    for profile_root in sorted((_ROOT / "profiles").iterdir()):
        if not profile_root.is_dir():
            continue
        profile = json.loads((profile_root / "profile.json").read_bytes())
        python_version = profile["python_version"]
        expected_from = f"FROM --platform=linux/amd64 {_BOOKWORM_BASES[python_version]}"
        instructions = (
            (profile_root / "Containerfile").read_text(encoding="utf-8").splitlines()
        )
        assert instructions.count(expected_from) == 1, profile_root.name


def _assert_source_build_recipe(
    containerfile: str,
    source_packages: tuple[str, ...],
) -> None:
    phase_one = "uv sync --locked --no-dev --no-install-project" + "".join(
        f" --no-install-package {package}" for package in source_packages
    )
    phase_two = "uv sync --locked --no-dev --no-install-project"

    assert containerfile.count("uv sync --locked") == 2
    assert "uv sync --frozen" not in containerfile
    assert containerfile.count(phase_one) == 1
    assert containerfile.count("--no-install-package") == len(source_packages)
    assert containerfile.count(_SETUPTOOLS_ASSERTION) == 1

    phase_one_index = containerfile.index(phase_one)
    assertion_index = containerfile.index(_SETUPTOOLS_ASSERTION)
    phase_two_index = containerfile.rindex(phase_two)
    assert phase_one_index < assertion_index < phase_two_index


def test_only_source_build_profiles_use_two_phase_locked_sync() -> None:
    profile_roots = sorted(
        profile_root
        for profile_root in (_ROOT / "profiles").iterdir()
        if profile_root.is_dir()
    )
    assert set(_SOURCE_BUILD_PROFILES) < {path.name for path in profile_roots}

    frozen_sync = "uv sync --frozen --no-dev --no-install-project"
    for profile_root in profile_roots:
        containerfile = (profile_root / "Containerfile").read_text(encoding="utf-8")
        source_packages = _SOURCE_BUILD_PROFILES.get(profile_root.name)
        if source_packages is not None:
            _assert_source_build_recipe(containerfile, source_packages)
            continue

        assert containerfile.count(frozen_sync) == 1, profile_root.name
        assert "uv sync --locked" not in containerfile, profile_root.name
        assert "--no-install-package" not in containerfile, profile_root.name
        assert _SETUPTOOLS_ASSERTION not in containerfile, profile_root.name


def test_taxi_deny_cleaner_is_available_only_through_a_build_only_mount() -> None:
    containerfile = (_ROOT / "profiles/rllib-taxi-synthesis/Containerfile").read_text(
        encoding="utf-8"
    )

    copied_cleaner = re.search(
        r"(?im)^\s*(?:COPY|ADD)\b[^\n]*\btaxi_deny\.py\b",
        containerfile,
    )
    assert copied_cleaner is None

    mount = "RUN --mount=type=bind,source=taxi_deny.py,target=/tmp/taxi_deny.py,ro"
    assert containerfile.count(mount) == 1
    assert containerfile.count(".venv/bin/python /tmp/taxi_deny.py harden") == 1
    assert containerfile.count(".venv/bin/python /tmp/taxi_deny.py verify") == 1
    assert containerfile.count("taxi_deny.py") == 4


def _assert_rebuildable_git_recipe(
    profile_id: str,
    containerfile: str,
    lockfile: str,
    repository_url: str,
    revision: str,
) -> None:
    _assert_source_build_recipe(containerfile, _SOURCE_BUILD_PROFILES[profile_id])
    snapshot_url = f"https://snapshot.debian.org/archive/debian/{_SNAPSHOT}"
    assert containerfile.count(snapshot_url) == 1
    assert "deb.debian.org" not in containerfile
    assert "security.debian.org" not in containerfile
    assert "rm -f /etc/apt/sources.list" in containerfile
    assert "/etc/apt/sources.list.d/debian.sources" in containerfile
    assert " bookworm main" in containerfile
    assert " trixie main" not in containerfile

    install = re.search(
        r"apt-get install -y --no-install-recommends (?P<packages>[^\\\n]+)",
        containerfile,
    )
    assert install is not None
    assert install.group("packages").split() == [
        f"ca-certificates={_CA_CERTIFICATES_VERSION}",
        f"git={_GIT_VERSION}",
    ]
    assert 'test "$(git --version)" = "git version 2.39.5"' in containerfile
    assert "test -s /etc/ssl/certs/ca-certificates.crt" in containerfile

    sync = "uv sync --locked --no-dev --no-install-project"
    assert containerfile.index("git --version") < containerfile.index(sync)

    exact_lock_source = (
        f'source = {{ git = "{repository_url}?rev={revision}#{revision}" }}'
    )
    assert exact_lock_source in lockfile


@pytest.mark.parametrize(
    ("profile_id", "repository_url", "revision"),
    [(profile_id, *source) for profile_id, source in sorted(_GIT_PROFILES.items())],
)
def test_active_git_profiles_install_from_a_frozen_snapshot_before_uv_sync(
    profile_id: str,
    repository_url: str,
    revision: str,
) -> None:
    profile_root = _ROOT / "profiles" / profile_id
    _assert_rebuildable_git_recipe(
        profile_id,
        (profile_root / "Containerfile").read_text(encoding="utf-8"),
        (profile_root / "uv.lock").read_text(encoding="utf-8"),
        repository_url,
        revision,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace(
            f"https://snapshot.debian.org/archive/debian/{_SNAPSHOT}",
            "https://deb.debian.org/debian",
        ),
        lambda text: text.replace(f"git={_GIT_VERSION}", "git"),
        lambda text: text.replace(
            f"ca-certificates={_CA_CERTIFICATES_VERSION}", "ca-certificates"
        ),
        lambda text: text.replace("uv sync --locked", "uv sync", 1),
    ],
    ids=["floating-repository", "floating-git", "floating-ca", "unlocked-sync"],
)
def test_git_recipe_contract_rejects_reproducibility_regressions(
    mutation: Callable[[str], str],
) -> None:
    repository_url, revision = _GIT_PROFILES["env-smacv2"]
    profile_root = _ROOT / "profiles" / "env-smacv2"
    containerfile = (profile_root / "Containerfile").read_text(encoding="utf-8")
    lockfile = (profile_root / "uv.lock").read_text(encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_rebuildable_git_recipe(
            "env-smacv2",
            mutation(containerfile),
            lockfile,
            repository_url,
            revision,
        )


def _import_smoke_job(workflow: str) -> str:
    marker = "\n  import-smoke:\n"
    assert workflow.count(marker) == 1
    return workflow.split(marker, maxsplit=1)[1]


def _assert_taxi_cache_isolation(workflow: str) -> None:
    job = _import_smoke_job(workflow)
    assert job.count("enable-cache: false") == 1
    assert job.count("restore-cache: false") == 1
    assert job.count("save-cache: false") == 1
    assert "enable-cache: true" not in job

    allowed_cache_templates = {
        'profile_cache="$(mktemp -d "/tmp/automarkov-taxi-uv-cache.XXXXXX")"',
        'profile_cache="$(mktemp -d "/var/tmp/automarkov-taxi-uv-cache.XXXXXX")"',
    }
    assert sum(job.count(template) for template in allowed_cache_templates) == 1
    assert "${RUNNER_TEMP}/automarkov-taxi-uv-cache.XXXXXX" not in job
    assert job.count('UV_CACHE_DIR="${profile_cache}" uv sync --locked') == 1
    assert job.count('harden --cache-root "${profile_cache}"') == 1
    assert job.count('verify --cache-root "${profile_cache}"') == 1
    assert job.count('rm -rf -- "${profile_cache}"') == 2
    assert job.count('test ! -e "${profile_cache}"') == 1
    assert "uv cache clean" not in job
    assert "/home/runner/.cache/uv" not in job
    assert "~/.cache/uv" not in job

    sync_index = job.index('UV_CACHE_DIR="${profile_cache}" uv sync --locked')
    harden_index = job.index('harden --cache-root "${profile_cache}"')
    verify_index = job.index('verify --cache-root "${profile_cache}"')
    cleanup_index = job.rindex('rm -rf -- "${profile_cache}"')
    absence_index = job.index('test ! -e "${profile_cache}"')
    assert sync_index < harden_index < verify_index < cleanup_index < absence_index


@pytest.mark.parametrize(
    "test_path",
    (
        "tests/contract/test_provenance_native_final_review.py",
        "tests/contract/test_provenance_native_identity_hardening.py",
        "tests/contract/test_provenance_native_publish_tree_closure.py",
        "tests/contract/test_provenance_native_review_closure.py",
    ),
)
def test_provenance_workflow_includes_native_reviews_in_metadata_gate(
    test_path: str,
) -> None:
    workflow = (_ROOT / ".github/workflows/provenance.yml").read_text(encoding="utf-8")
    metadata = workflow.split("\n  metadata:\n", maxsplit=1)[1].split(
        "\n  import-smoke:\n", maxsplit=1
    )[0]

    pytest_lines = [line for line in metadata.splitlines() if "pytest -q " in line]
    assert len(pytest_lines) == 1
    assert test_path in pytest_lines[0]


def test_provenance_metadata_security_gate_runs_for_every_changed_path() -> None:
    workflow = (_ROOT / ".github/workflows/provenance.yml").read_text(encoding="utf-8")
    push = workflow.split("  push:\n", maxsplit=1)[1].split(
        "  pull_request:\n", maxsplit=1
    )[0]
    pull_request = workflow.split("  pull_request:\n", maxsplit=1)[1].split(
        "  workflow_dispatch:\n", maxsplit=1
    )[0]
    metadata = workflow.split("\n  metadata:\n", maxsplit=1)[1].split(
        "\n  import-smoke:\n", maxsplit=1
    )[0]
    import_smoke = workflow.split("\n  import-smoke:\n", maxsplit=1)[1]

    for event_filter in (push, pull_request):
        assert "paths:" not in event_filter
        assert "paths-ignore:" not in event_filter
    assert not any(line.startswith("    if:") for line in metadata.splitlines())
    assert "automarkov verify-provenance --repository-root ." in metadata
    assert "if: github.event_name == 'workflow_dispatch'" in import_smoke


def test_workflow_dispatch_taxi_uses_only_a_disposable_uv_cache() -> None:
    workflow = (_ROOT / ".github/workflows/provenance.yml").read_text(encoding="utf-8")
    metadata = workflow.split("\n  metadata:\n", maxsplit=1)[1].split(
        "\n  import-smoke:\n", maxsplit=1
    )[0]
    for test_path in (
        "tests/contract/test_profile_recipe_workflow.py",
        "tests/contract/test_provenance_review_regressions.py",
    ):
        assert test_path in metadata.split("pytest -q ", maxsplit=1)[1]
    _assert_taxi_cache_isolation(workflow)


def test_workflow_dispatch_taxi_cache_root_matches_deny_layer_allowlist() -> None:
    workflow = (_ROOT / ".github/workflows/provenance.yml").read_text(encoding="utf-8")
    _assert_taxi_cache_isolation(workflow)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace("enable-cache: false", "enable-cache: true", 1),
        lambda text: re.sub(
            r'"/(?:var/)?tmp/automarkov-taxi-uv-cache\.XXXXXX"',
            '"${HOME}/.cache/uv"',
            text,
            count=1,
        ),
        lambda text: text.replace(
            'test ! -e "${profile_cache}"',
            ":",
            1,
        ),
    ],
    ids=["setup-uv-cache", "shared-cache", "missing-deletion-proof"],
)
def test_taxi_cache_contract_rejects_shared_or_unverified_cache(
    mutation: Callable[[str], str],
) -> None:
    workflow = (_ROOT / ".github/workflows/provenance.yml").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_taxi_cache_isolation(mutation(workflow))
