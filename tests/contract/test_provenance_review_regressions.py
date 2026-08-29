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
_COPY_IGNORE = shutil.ignore_patterns(".venv", "__pycache__")


@pytest.fixture
def repository_copy(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative_path in ("profiles", "references", "src", "docs", ".github"):
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


def _package_block(lock: str, package_name: str) -> tuple[int, int, str]:
    marker = f'[[package]]\nname = "{package_name}"\n'
    start = lock.index(marker)
    next_start = lock.find("\n[[package]]", start + len(marker))
    end = len(lock) if next_start == -1 else next_start + 1
    return start, end, lock[start:end]


def _replace_or_append_lock_package(
    target_path: Path,
    source_path: Path,
    package_name: str,
) -> None:
    target = target_path.read_text(encoding="utf-8")
    source = source_path.read_text(encoding="utf-8")
    _, _, source_block = _package_block(source, package_name)
    marker = f'[[package]]\nname = "{package_name}"\n'
    if marker in target:
        start, end, _ = _package_block(target, package_name)
        updated = target[:start] + source_block + target[end:]
    else:
        updated = target.rstrip() + "\n\n" + source_block
    target_path.write_text(updated, encoding="utf-8")


def _named_entries(
    payload: dict[str, object],
    collection_name: str,
) -> list[dict[str, object]]:
    entries = payload[collection_name]
    assert type(entries) is list
    assert all(type(entry) is dict for entry in entries)
    return cast(list[dict[str, object]], entries)


def _replace_or_append_named_entry(
    target: list[dict[str, object]],
    source: list[dict[str, object]],
    name: str,
    *,
    version_field: str,
    preserve_spdx_id: bool = False,
) -> None:
    source_entry = next(entry for entry in source if entry.get("name") == name)
    replacement = cast(
        dict[str, object],
        json.loads(json.dumps(source_entry)),
    )
    target_index = next(
        (index for index, entry in enumerate(target) if entry.get("name") == name),
        None,
    )
    if target_index is None:
        if preserve_spdx_id:
            replacement["SPDXID"] = f"SPDXRef-Package-{name}-review"
        target.append(replacement)
    else:
        if preserve_spdx_id:
            replacement["SPDXID"] = target[target_index]["SPDXID"]
        target[target_index] = replacement
    target.sort(
        key=lambda entry: (
            cast(str, entry["name"]).encode("utf-8"),
            cast(str, entry[version_field]).encode("utf-8"),
        )
    )


def _replace_minigrid_gymnasium(repository: Path) -> None:
    target_root = repository / "profiles" / "env-minigrid"
    source_root = repository / "profiles" / "sealed-env-taxi-gold"
    lock_path = target_root / "uv.lock"
    _replace_or_append_lock_package(
        lock_path,
        source_root / "uv.lock",
        "gymnasium",
    )
    lock = lock_path.read_text(encoding="utf-8")
    assert lock.count('specifier = "==1.2.2"') == 1
    lock_path.write_text(
        lock.replace('specifier = "==1.2.2"', 'specifier = "==1.3.0"'),
        encoding="utf-8",
    )
    pyproject_path = target_root / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")
    assert pyproject.count("gymnasium==1.2.2") == 1
    pyproject_path.write_text(
        pyproject.replace("gymnasium==1.2.2", "gymnasium==1.3.0"),
        encoding="utf-8",
    )

    target_sbom_path = target_root / "sbom.spdx.json"
    target_sbom = _read_json_object(target_sbom_path)
    source_sbom = _read_json_object(source_root / "sbom.spdx.json")
    _replace_or_append_named_entry(
        _named_entries(target_sbom, "packages"),
        _named_entries(source_sbom, "packages"),
        "gymnasium",
        version_field="versionInfo",
        preserve_spdx_id=True,
    )
    _write_json(target_sbom_path, target_sbom)

    target_license_path = target_root / "license-manifest.json"
    target_license = _read_json_object(target_license_path)
    source_license = _read_json_object(source_root / "license-manifest.json")
    _replace_or_append_named_entry(
        _named_entries(target_license, "dependencies"),
        _named_entries(source_license, "dependencies"),
        "gymnasium",
        version_field="version",
    )
    _write_json(target_license_path, target_license)

    profile_path = target_root / "profile.json"
    profile = _read_json_object(profile_path)
    package_versions = profile["package_versions"]
    assert type(package_versions) is dict
    package_versions["gymnasium"] = "1.3.0"
    _write_json(profile_path, profile)

    smoke_path = target_root / "smoke.json"
    smoke = _read_json_object(smoke_path)
    imports = smoke["imports"]
    assert type(imports) is list and "gymnasium" in imports
    _write_json(smoke_path, smoke)
    _rehash_profile(repository, "env-minigrid")


def _inject_authoring_tavily(repository: Path) -> None:
    target_root = repository / "profiles" / "authoring"
    source_root = repository / "profiles" / "retrieval-tavily"
    lock_path = target_root / "uv.lock"
    _replace_or_append_lock_package(
        lock_path,
        source_root / "uv.lock",
        "tavily-python",
    )
    lock = lock_path.read_text(encoding="utf-8")
    start, end, root_block = _package_block(lock, "automarkov-profile-authoring")
    root_block = root_block.replace(
        '    { name = "sentence-transformers" },\n    { name = "setuptools" },\n]',
        '    { name = "sentence-transformers" },\n'
        '    { name = "setuptools" },\n'
        '    { name = "tavily-python" },\n]',
        1,
    ).replace(
        '    { name = "sentence-transformers", specifier = "==5.7.0" },\n'
        '    { name = "setuptools", specifier = "==84.0.0" },\n]',
        '    { name = "sentence-transformers", specifier = "==5.7.0" },\n'
        '    { name = "setuptools", specifier = "==84.0.0" },\n'
        '    { name = "tavily-python", specifier = "==0.7.27" },\n]',
        1,
    )
    assert root_block.count('name = "tavily-python"') == 2
    lock_path.write_text(lock[:start] + root_block + lock[end:], encoding="utf-8")

    pyproject_path = target_root / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")
    assert "tavily-python" not in pyproject
    pyproject = pyproject.replace(
        '  "sentence-transformers==5.7.0",\n'
        '  "lancedb==0.36.0",\n'
        '  "setuptools==84.0.0",\n]',
        '  "sentence-transformers==5.7.0",\n'
        '  "lancedb==0.36.0",\n'
        '  "setuptools==84.0.0",\n'
        '  "tavily-python==0.7.27",\n]',
    )
    assert pyproject.count("tavily-python==0.7.27") == 1
    pyproject_path.write_text(pyproject, encoding="utf-8")

    target_sbom_path = target_root / "sbom.spdx.json"
    target_sbom = _read_json_object(target_sbom_path)
    source_sbom = _read_json_object(source_root / "sbom.spdx.json")
    _replace_or_append_named_entry(
        _named_entries(target_sbom, "packages"),
        _named_entries(source_sbom, "packages"),
        "tavily-python",
        version_field="versionInfo",
        preserve_spdx_id=True,
    )
    _write_json(target_sbom_path, target_sbom)

    target_license_path = target_root / "license-manifest.json"
    target_license = _read_json_object(target_license_path)
    source_license = _read_json_object(source_root / "license-manifest.json")
    _replace_or_append_named_entry(
        _named_entries(target_license, "dependencies"),
        _named_entries(source_license, "dependencies"),
        "tavily-python",
        version_field="version",
    )
    _write_json(target_license_path, target_license)

    profile_path = target_root / "profile.json"
    profile = _read_json_object(profile_path)
    package_versions = profile["package_versions"]
    assert type(package_versions) is dict
    package_versions["tavily-python"] = "0.7.27"
    _write_json(profile_path, profile)

    smoke_path = target_root / "smoke.json"
    smoke = _read_json_object(smoke_path)
    imports = smoke["imports"]
    forbidden_imports = smoke["forbidden_imports"]
    assert type(imports) is list and type(forbidden_imports) is list
    imports.append("tavily")
    imports.sort()
    forbidden_imports.remove("tavily")
    _write_json(smoke_path, smoke)
    _rehash_profile(repository, "authoring")


def test_restricted_source_import_is_rejected_without_flagging_declarations(
    repository_copy: Path,
) -> None:
    manifest = (repository_copy / "references" / "manifest.yaml").read_text(
        encoding="utf-8"
    )
    research = (
        repository_copy
        / "docs"
        / "research"
        / "2026-08-10-t04-upstream-bom-and-profile-isolation.md"
    ).read_text(encoding="utf-8")
    assert "agent2world" in manifest.lower()
    assert "agent2world" in research.lower()

    ingress = repository_copy / "src" / "automarkov" / "restricted_ingress.py"
    ingress.write_text("import agent2world\n", encoding="utf-8")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "agent2world", "src/automarkov/restricted_ingress.py")


@pytest.mark.parametrize(
    ("host_layer", "expected_error", "unexpected_host_errors"),
    (
        (
            "registry",
            "core: unapproved registry host: 'packages.review.invalid'",
            ("unapproved artifact host", "unapproved upstream host"),
        ),
        (
            "artifact",
            "core: unapproved artifact host: 'packages.review.invalid'",
            ("unapproved registry host", "unapproved upstream host"),
        ),
        (
            "active-upstream",
            "pydantic: unapproved upstream host: 'huggingface.co'",
            ("unapproved registry host", "unapproved artifact host"),
        ),
    ),
)
def test_each_unapproved_https_host_layer_is_rejected_independently(
    repository_copy: Path,
    host_layer: str,
    expected_error: str,
    unexpected_host_errors: tuple[str, ...],
) -> None:
    if host_layer in {"registry", "artifact"}:
        hostile_origin = "https://packages.review.invalid"
        profile_root = repository_copy / "profiles" / "core"
        lock_path = profile_root / "uv.lock"
        lock = lock_path.read_text(encoding="utf-8")
        start, end, pydantic_block = _package_block(lock, "pydantic")

        sbom_path = profile_root / "sbom.spdx.json"
        sbom = _read_json_object(sbom_path)
        packages = sbom["packages"]
        assert type(packages) is list
        pydantic_package = next(
            package
            for package in packages
            if type(package) is dict and package.get("name") == "pydantic"
        )

        if host_layer == "registry":
            pydantic_block = pydantic_block.replace(
                "https://pypi.org/simple",
                f"{hostile_origin}/simple",
                1,
            )
            pydantic_package["sourceInfo"] = canonical_json_bytes(
                {"registry": f"{hostile_origin}/simple"}
            ).decode("utf-8")
        else:
            download_location = pydantic_package["downloadLocation"]
            assert type(download_location) is str
            hostile_download = download_location.replace(
                "https://files.pythonhosted.org",
                hostile_origin,
                1,
            )
            assert pydantic_block.count(download_location) == 1
            pydantic_block = pydantic_block.replace(
                download_location,
                hostile_download,
                1,
            )
            pydantic_package["downloadLocation"] = hostile_download

        lock_path.write_text(
            lock[:start] + pydantic_block + lock[end:],
            encoding="utf-8",
        )
        _write_json(sbom_path, sbom)
        _rehash_profile(repository_copy, "core")
    else:
        assert host_layer == "active-upstream"
        catalog_path = repository_copy / "references" / "manifest.yaml"
        catalog = json.loads(catalog_path.read_bytes())
        assert type(catalog) is list
        pydantic_upstream = next(
            item
            for item in catalog
            if type(item) is dict and item.get("resource_id") == "pydantic"
        )
        pydantic_upstream["repository"] = "https://huggingface.co/pydantic/pydantic"
        _write_json(catalog_path, catalog)

    report = verify_provenance(repository_copy)

    assert report.valid is False
    assert expected_error in report.errors
    assert not any(
        fragment in error
        for error in report.errors
        for fragment in unexpected_host_errors
    ), report.errors
    assert not any(
        fragment in error
        for error in report.errors
        for fragment in (
            "hash mismatch",
            "SBOM source does not match lock",
            "SBOM checksum does not match lock",
        )
    ), report.errors


@pytest.mark.parametrize(
    ("policy_case", "expected_error"),
    (
        ("minigrid-gymnasium-1.3.0", ("env-minigrid", "gymnasium", "1.3.0")),
        ("authoring-tavily", ("authoring", "tavily")),
    ),
)
def test_central_profile_policy_rejects_self_consistent_forbidden_packages(
    repository_copy: Path,
    policy_case: str,
    expected_error: tuple[str, ...],
) -> None:
    if policy_case == "minigrid-gymnasium-1.3.0":
        _replace_minigrid_gymnasium(repository_copy)
    else:
        assert policy_case == "authoring-tavily"
        _inject_authoring_tavily(repository_copy)

    report = verify_provenance(repository_copy)

    _assert_rejected(report, *expected_error)
    assert not any(
        fragment in error
        for error in report.errors
        for fragment in (
            "hash mismatch",
            "SBOM does not cover exact lock",
            "license manifest does not cover exact lock",
            "package summary does not match lock",
        )
    ), report.errors


@pytest.mark.parametrize(
    "containerfile_case",
    ("alpine-base", "wrong-python", "unlocked-install"),
)
def test_containerfile_policy_survives_build_context_rehash(
    repository_copy: Path,
    containerfile_case: str,
) -> None:
    containerfile_path = repository_copy / "profiles" / "core" / "Containerfile"
    containerfile = containerfile_path.read_text(encoding="utf-8")
    if containerfile_case == "alpine-base":
        from_line = next(
            line for line in containerfile.splitlines() if line.startswith("FROM ")
        )
        replacement = "FROM --platform=linux/amd64 alpine:3.22@sha256:" + "a" * 64
        containerfile = containerfile.replace(from_line, replacement, 1)
    elif containerfile_case == "wrong-python":
        assert "python:3.12.11-slim" in containerfile
        containerfile = containerfile.replace(
            "python:3.12.11-slim",
            "python:3.11.13-slim",
            1,
        )
    else:
        assert containerfile_case == "unlocked-install"
        assert "RUN uv sync --frozen --no-dev --no-install-project" in containerfile
        containerfile = containerfile.replace(
            "RUN uv sync --frozen --no-dev --no-install-project",
            "RUN uv sync --no-dev --no-install-project",
            1,
        )
    containerfile_path.write_text(containerfile, encoding="utf-8")
    _rehash_profile(repository_copy, "core")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "containerfile")
    assert not any("build context hash mismatch" in error for error in report.errors), (
        report.errors
    )


def test_smoke_schema_version_is_closed_after_contract_rehash(
    repository_copy: Path,
) -> None:
    smoke_path = repository_copy / "profiles" / "core" / "smoke.json"
    smoke = _read_json_object(smoke_path)
    smoke["schema_version"] = "arbitrary-review-schema.v999"
    _write_json(smoke_path, smoke)
    _rehash_profile(repository_copy, "core")

    report = verify_provenance(repository_copy)

    _assert_rejected(report, "core", "smoke", "schema")
    assert not any("hash mismatch" in error for error in report.errors), report.errors
