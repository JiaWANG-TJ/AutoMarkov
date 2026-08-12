from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from automarkov.pilots import (
    DISCLAIMER,
    EngineeringPilotManifest,
    _profile_preflight,
    load_pilot_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "pilots/cartpole_cpu_smoke.v1.json"


def test_manifest_freezes_nonconfirmatory_cartpole_rllib_cpu_contract() -> None:
    manifest = load_pilot_manifest(MANIFEST)
    assert manifest.pilot_id == "cartpole_cpu_smoke_v1"
    assert (manifest.purpose, manifest.experiment_eligibility) == (
        "engineering",
        "nonconfirmatory",
    )
    assert manifest.publication_eligible is False
    assert manifest.runtime_profile.package_versions.model_dump() == {
        "gymnasium": "1.2.2",
        "ray": "2.56.1",
        "torch": "2.13.0",
    }
    assert manifest.runtime_profile.python_version == "3.11.13"
    assert manifest.runtime_profile.worker_interpreter == (
        "artifacts/pilots/runtime/rllib-core-py31113/bin/python"
    )
    assert manifest.workload.environment_id == "CartPole-v1"
    assert manifest.workload.algorithm == "PPO"
    assert manifest.workload.num_env_runners == manifest.workload.num_learners == 0
    assert manifest.workload.num_gpus_per_learner == 0
    assert manifest.limits.wall_time_seconds == 300
    assert DISCLAIMER.startswith("NONCONFIRMATORY engineering evidence")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("purpose",), "experiment"),
        (("experiment_eligibility",), "confirmatory"),
        (("publication_eligible",), True),
        (("runtime_profile", "package_versions", "ray"), "2.55.0"),
        (("workload", "environment_id"), "Taxi-v3"),
        (("workload", "num_gpus_per_learner"), 1),
    ],
)
def test_manifest_rejects_claim_or_runtime_drift(
    path: tuple[str, ...], replacement: object
) -> None:
    payload = json.loads(MANIFEST.read_bytes())
    cursor = payload
    for segment in path[:-1]:
        cursor = cursor[segment]
    cursor[path[-1]] = replacement
    with pytest.raises(ValidationError):
        EngineeringPilotManifest.model_validate(payload, strict=True)


def test_manifest_is_closed() -> None:
    payload = json.loads(MANIFEST.read_bytes())
    payload["formal_run_id"] = "run_forbidden"
    with pytest.raises(ValidationError):
        EngineeringPilotManifest.model_validate(payload, strict=True)


@pytest.mark.parametrize("drift", ["profile", "lock"])
def test_runtime_preflight_recomputes_profile_and_lock_identity(
    tmp_path: Path, drift: str
) -> None:
    profile_root = tmp_path / "profiles/rllib-core"
    profile_root.mkdir(parents=True)
    shutil.copy2(
        ROOT / "profiles/rllib-core/profile.json", profile_root / "profile.json"
    )
    shutil.copy2(ROOT / "profiles/rllib-core/uv.lock", profile_root / "uv.lock")
    interpreter = tmp_path / "artifacts/pilots/runtime/rllib-core-py31113/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(
        ROOT / "artifacts/pilots/runtime/rllib-core-py31113/bin/python"
    )
    if drift == "profile":
        payload = json.loads((profile_root / "profile.json").read_bytes())
        payload["hardware_contract"] = "cpu-drift"
        (profile_root / "profile.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    else:
        (profile_root / "uv.lock").write_bytes(
            (profile_root / "uv.lock").read_bytes() + b"\n# drift\n"
        )

    with pytest.raises(ValueError, match="identity"):
        _profile_preflight(load_pilot_manifest(MANIFEST), tmp_path)
