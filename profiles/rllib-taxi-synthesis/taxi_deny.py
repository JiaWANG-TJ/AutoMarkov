from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib
import importlib.resources
import importlib.util
import shutil
import site
import sys
from pathlib import Path

_REGISTRY_BLOCK = """register(
    id="Taxi-v3",
    entry_point="gymnasium.envs.toy_text.taxi:TaxiEnv",
    reward_threshold=8,  # optimum = 8.46
    max_episode_steps=200,
)

"""
_TOY_TEXT_IMPORT = "from gymnasium.envs.toy_text.taxi import TaxiEnv\n"


def _default_site_packages() -> Path:
    candidates = [Path(path) for path in site.getsitepackages()]
    matches = [path for path in candidates if (path / "gymnasium").is_dir()]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one installed Gymnasium distribution")
    return matches[0]


def _replace_once(path: Path, needle: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(needle) != 1:
        raise RuntimeError(f"unexpected Gymnasium 1.2.2 source layout: {path}")
    path.write_text(text.replace(needle, ""), encoding="utf-8")


def _record_digest(path: Path) -> tuple[str, str]:
    payload = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return f"sha256={digest.decode('ascii')}", str(len(payload))


def _rewrite_record(site_packages: Path, changed: tuple[Path, ...]) -> None:
    records = tuple(site_packages.glob("gymnasium-*.dist-info/RECORD"))
    if not records:
        return
    if len(records) != 1:
        raise RuntimeError("expected exactly one Gymnasium RECORD")
    record = records[0]
    changed_by_relative = {
        path.relative_to(site_packages).as_posix(): path for path in changed
    }
    rows: list[list[str]] = []
    with record.open(newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if "taxi" in row[0].lower():
                continue
            changed_path = changed_by_relative.get(row[0])
            if changed_path is not None:
                digest, size = _record_digest(changed_path)
                row = [row[0], digest, size]
            rows.append(row)
    with record.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)


def _require_controlled_cache_root(cache_root: Path) -> Path:
    if not cache_root.is_absolute() or any(
        part in {".", ".."} for part in cache_root.parts
    ):
        raise ValueError("cache root must be a controlled temporary directory")
    lexical = Path(cache_root.anchor)
    for part in cache_root.parts[1:]:
        lexical /= part
        if lexical.is_symlink():
            raise ValueError("cache root must not contain symlink ancestors")
    resolved = cache_root.resolve(strict=False)
    allowed_parents = (Path("/tmp").resolve(), Path("/var/tmp").resolve())
    if resolved in allowed_parents or not any(
        resolved.is_relative_to(parent) for parent in allowed_parents
    ):
        raise ValueError("cache root must be a controlled temporary directory")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("cache root must be a directory")
    return resolved


def harden(site_packages: Path, cache_roots: tuple[Path, ...]) -> None:
    gymnasium_root = site_packages / "gymnasium"
    envs_init = gymnasium_root / "envs" / "__init__.py"
    toy_init = gymnasium_root / "envs" / "toy_text" / "__init__.py"
    if not gymnasium_root.is_dir():
        raise RuntimeError("Gymnasium is not installed in the selected site-packages")

    _replace_once(envs_init, _REGISTRY_BLOCK)
    _replace_once(toy_init, _TOY_TEXT_IMPORT)
    for path in tuple(gymnasium_root.rglob("*")):
        if "taxi" in path.name.lower() and path.is_file():
            path.unlink()
    for pycache in sorted(gymnasium_root.rglob("__pycache__"), reverse=True):
        shutil.rmtree(pycache)
    _rewrite_record(site_packages, (envs_init, toy_init))
    for cache_root in cache_roots:
        controlled_root = _require_controlled_cache_root(cache_root)
        if controlled_root.exists():
            shutil.rmtree(controlled_root)


def verify(site_packages: Path, cache_roots: tuple[Path, ...]) -> None:
    gymnasium_root = site_packages / "gymnasium"
    distribution_roots = (gymnasium_root, *site_packages.glob("gymnasium-*.dist-info"))
    leaks: list[str] = []
    for root in distribution_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "taxi" in path.name.lower() or b"taxi" in path.read_bytes().lower():
                leaks.append(str(path))
    for cache_root in cache_roots:
        controlled_root = _require_controlled_cache_root(cache_root)
        if controlled_root.exists() and any(controlled_root.rglob("*")):
            leaks.append(str(controlled_root))
    if leaks:
        raise RuntimeError(f"Taxi deny-layer leaks remain: {sorted(leaks)}")

    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(site_packages))
    importlib.invalidate_caches()
    try:
        try:
            if importlib.util.find_spec("gymnasium.envs.toy_text.taxi") is not None:
                raise RuntimeError("Taxi module remains discoverable")
        except ModuleNotFoundError:
            pass
        try:
            importlib.import_module("gymnasium.envs.toy_text.taxi")
        except ModuleNotFoundError:
            pass
        else:
            raise RuntimeError("Taxi module remains importable")
        taxi_resource = importlib.resources.files("gymnasium.envs.toy_text") / "taxi.py"
        try:
            taxi_resource.read_bytes()
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("Taxi resource remains readable")
    finally:
        sys.path.remove(str(site_packages))
        sys.dont_write_bytecode = previous_dont_write_bytecode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--cache-root", action="append", default=[], type=Path)
    parser.add_argument("action", choices=("harden", "verify"))
    args = parser.parse_args()
    site_packages = args.site_packages or _default_site_packages()
    cache_roots = tuple(
        _require_controlled_cache_root(path) for path in args.cache_root
    )
    if args.action == "harden":
        harden(site_packages, cache_roots)
    else:
        verify(site_packages, cache_roots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
