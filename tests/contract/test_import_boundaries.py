from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = _REPOSITORY_ROOT / "src" / "automarkov"
_SEAM_ROOTS = ("automarkov.domain", "automarkov.public")
_ALLOWED_INTERNAL_PREFIXES = (
    "automarkov.domain",
    "automarkov.errors",
    "automarkov.public",
)
_ALLOWED_EXTERNAL_ROOTS = {"__future__", "pydantic"}
_FORBIDDEN_RUNTIME_ROOTS = {
    "camel",
    "gymnasium",
    "httpx",
    "numpy",
    "openai",
    "pettingzoo",
    "ray",
    "swanlab",
    "tavily",
    "torch",
    "transformers",
    "vllm",
}


def _module_name(source: Path) -> str:
    relative = source.relative_to(_PACKAGE_ROOT)
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("automarkov", *parts))


def _is_package(source: Path) -> bool:
    return source.name == "__init__.py"


def _matches_prefix(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in prefixes
    )


def _imported_modules(
    source: Path,
    known_modules: set[str],
) -> list[tuple[int, str]]:
    current_module = _module_name(source)
    current_package = (
        current_module if _is_package(source) else current_module.rpartition(".")[0]
    )
    imports: list[tuple[int, str]] = []

    for node in ast.walk(
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    ):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            package_parts = current_package.split(".")
            parent_count = node.level - 1
            base_parts = package_parts[: len(package_parts) - parent_count]
            if node.module:
                base_parts.extend(node.module.split("."))
            base = ".".join(base_parts)
        else:
            base = node.module or ""

        if base:
            imports.append((node.lineno, base))
        for alias in node.names:
            candidate = f"{base}.{alias.name}" if base else alias.name
            if candidate in known_modules:
                imports.append((node.lineno, candidate))

    return imports


def test_domain_and_public_static_import_closure_is_infrastructure_free() -> None:
    sources = sorted(_PACKAGE_ROOT.rglob("*.py")) if _PACKAGE_ROOT.is_dir() else []
    modules = {_module_name(source): source for source in sources}
    missing_seams = [seam for seam in _SEAM_ROOTS if seam not in modules]
    assert not missing_seams, f"missing public seam packages: {missing_seams}"

    seam_sources = {
        module_name: source
        for module_name, source in modules.items()
        if _matches_prefix(module_name, _ALLOWED_INTERNAL_PREFIXES)
    }
    violations: list[str] = []
    for module_name, source in sorted(seam_sources.items()):
        for line_number, imported_module in _imported_modules(source, set(modules)):
            if imported_module == "automarkov":
                continue
            if imported_module.startswith("automarkov."):
                allowed = _matches_prefix(
                    imported_module,
                    _ALLOWED_INTERNAL_PREFIXES,
                )
            else:
                root = imported_module.partition(".")[0]
                allowed = (
                    root in sys.stdlib_module_names or root in _ALLOWED_EXTERNAL_ROOTS
                )
            if not allowed:
                relative_source = source.relative_to(_REPOSITORY_ROOT)
                violations.append(
                    f"{relative_source}:{line_number}: "
                    f"{module_name} imports {imported_module}"
                )

    assert not violations, (
        "infrastructure crossed the domain/public seam:\n" + "\n".join(violations)
    )


def test_importing_domain_and_public_has_no_infrastructure_side_effects() -> None:
    script = f"""
import importlib
import sys

before = set(sys.modules)
importlib.import_module("automarkov.domain")
importlib.import_module("automarkov.public")
loaded = set(sys.modules) - before
forbidden_runtime_roots = {sorted(_FORBIDDEN_RUNTIME_ROOTS)!r}
forbidden_internal_prefixes = (
    "automarkov.adapters",
    "automarkov.api",
    "automarkov.cli",
    "automarkov.orchestration",
)
violations = sorted(
    module_name
    for module_name in loaded
    if module_name.partition(".")[0] in forbidden_runtime_roots
    or any(
        module_name == prefix or module_name.startswith(prefix + ".")
        for prefix in forbidden_internal_prefixes
    )
)
if violations:
    raise SystemExit("infrastructure imported as a side effect: " + ", ".join(violations))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
