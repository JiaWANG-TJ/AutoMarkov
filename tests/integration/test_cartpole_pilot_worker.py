from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from automarkov.pilot_worker import main as worker_main
from automarkov.pilots import (
    EngineeringPilotWorkerResponse,
    build_worker_request,
    load_pilot_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "pilots/cartpole_cpu_smoke.v1.json"


def _request_payload() -> dict[str, object]:
    return build_worker_request(load_pilot_manifest(MANIFEST)).model_dump(mode="json")


def _fake_trainer(request: dict[str, object]) -> dict[str, object]:
    assert request["pilot_id"] == "cartpole_cpu_smoke_v1"
    return {
        "schema_version": "automarkov.engineering-pilot-worker-response.v1",
        "pilot_id": "cartpole_cpu_smoke_v1",
        "status": "success",
        "package_versions": {"gymnasium": "1.2.2", "ray": "2.56.1", "torch": "2.13.0"},
        "python_version": "3.11.13",
        "training": {
            "iterations_completed": 1,
            "num_env_steps_sampled_lifetime": 512,
            "episode_return_mean_decimal": "20",
            "episode_length_mean_decimal": "20",
        },
        "evaluation": {
            "episodes_completed": 1,
            "episode_return_mean_decimal": "40",
            "episode_length_mean_decimal": "40",
        },
        "reason_code": None,
        "error_type": None,
        "error_message": None,
    }


def test_worker_uses_closed_json_file_protocol(tmp_path: Path) -> None:
    request_path, result_path = tmp_path / "request.json", tmp_path / "result.json"
    request_path.write_text(json.dumps(_request_payload()), encoding="utf-8")
    assert (
        worker_main(
            ["--request", str(request_path), "--result", str(result_path)],
            trainer=_fake_trainer,
        )
        == 0
    )
    assert (
        EngineeringPilotWorkerResponse.model_validate_json(
            result_path.read_bytes(), strict=True
        ).status
        == "success"
    )
    assert (
        EngineeringPilotWorkerResponse.model_validate_json(
            result_path.read_bytes(), strict=True
        ).python_version
        == "3.11.13"
    )


def test_worker_rejects_contract_drift_before_training(tmp_path: Path) -> None:
    payload = _request_payload()
    payload["environment_id"] = "Taxi-v3"
    request_path, result_path = tmp_path / "request.json", tmp_path / "result.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="closed P0 contract"):
        worker_main(
            ["--request", str(request_path), "--result", str(result_path)],
            trainer=_fake_trainer,
        )
    assert not result_path.exists()


def test_worker_never_returns_zero_for_a_terminal_failure_response(
    tmp_path: Path,
) -> None:
    def terminal_failure(_: dict[str, object]) -> dict[str, object]:
        response = _fake_trainer(_request_payload())
        response.update(
            {
                "status": "terminal_failure",
                "training": None,
                "evaluation": None,
                "reason_code": "forced_failure",
                "error_type": "RuntimeError",
                "error_message": "forced",
            }
        )
        return response

    request_path, result_path = tmp_path / "request.json", tmp_path / "result.json"
    request_path.write_text(json.dumps(_request_payload()), encoding="utf-8")
    assert (
        worker_main(
            ["--request", str(request_path), "--result", str(result_path)],
            trainer=terminal_failure,
        )
        == 4
    )
    assert (
        EngineeringPilotWorkerResponse.model_validate_json(
            result_path.read_bytes(), strict=True
        ).status
        == "terminal_failure"
    )


@pytest.mark.skipif(
    os.environ.get("AUTOMARKOV_RUN_RLLIB_PILOT") != "1",
    reason="真实 RLlib runtime gate 必须显式启用",
)
def test_frozen_profile_runs_real_cartpole_rllib_worker(tmp_path: Path) -> None:
    request_path, result_path = tmp_path / "request.json", tmp_path / "result.json"
    request_path.write_text(json.dumps(_request_payload()), encoding="utf-8")
    completed = subprocess.run(
        [
            str(ROOT / "artifacts/pilots/runtime/rllib-core-py31113/bin/python"),
            "-m",
            "automarkov.pilot_worker",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert (
        EngineeringPilotWorkerResponse.model_validate_json(
            result_path.read_bytes(), strict=True
        ).status
        == "success"
    )
