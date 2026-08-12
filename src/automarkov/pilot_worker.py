from __future__ import annotations

import argparse
import json
import math
import operator
import platform
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from typing import Any, cast

_EXPECTED_REQUEST = {
    "schema_version": "automarkov.engineering-pilot-worker-request.v1",
    "pilot_id": "cartpole_cpu_smoke_v1",
    "profile_id": "rllib-core",
    "environment_id": "CartPole-v1",
    "algorithm": "PPO",
    "framework": "torch",
    "seed": 20260812,
    "train_iterations": 1,
    "train_batch_size_per_learner": 512,
    "minibatch_size": 128,
    "num_epochs": 2,
    "num_env_runners": 0,
    "num_envs_per_env_runner": 1,
    "num_learners": 0,
    "num_gpus_per_learner": 0,
    "evaluation_episodes": 1,
    "evaluation_explore": False,
    "checkpoint_policy": "none",
    "wall_time_seconds": 300,
    "ray_num_cpus": 2,
    "max_stdout_bytes": 1048576,
    "max_stderr_bytes": 1048576,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automarkov.pilot_worker")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    return parser


def _load_request(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_bytes())
    if type(payload) is not dict or payload != _EXPECTED_REQUEST:
        raise ValueError("worker request does not match the closed P0 contract")
    if any(
        type(payload[key]) is not type(value)
        for key, value in _EXPECTED_REQUEST.items()
    ):
        raise ValueError(
            "worker request scalar types do not match the closed P0 contract"
        )
    return cast(dict[str, object], payload)


def _decimal_string(value: object) -> str:
    if type(value) is bool:
        raise ValueError("RLlib metric must be a finite number")
    try:
        finite_value = float(cast(Any, value))
    except (TypeError, ValueError) as error:
        raise ValueError("RLlib metric must be a finite number") from error
    if not math.isfinite(finite_value):
        raise ValueError("RLlib metric must be a finite number")
    decimal = Decimal(str(value))
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _metrics_group(result: Mapping[str, object]) -> Mapping[str, object]:
    group = result.get("env_runners")
    if not isinstance(group, Mapping):
        raise TypeError("RLlib result omitted new-stack env_runners metrics")
    return cast(Mapping[str, object], group)


def _run_rllib(request: dict[str, object]) -> dict[str, object]:
    import ray  # pyright: ignore[reportMissingImports]
    from ray.rllib.algorithms.ppo import (  # pyright: ignore[reportMissingImports]
        PPOConfig,
    )

    package_versions = {
        "gymnasium": version("gymnasium"),
        "ray": version("ray"),
        "torch": version("torch"),
    }
    python_version = platform.python_version()
    if python_version != "3.11.13" or tuple(package_versions.values()) != (
        "1.2.2",
        "2.56.1",
        "2.13.0",
    ):
        raise RuntimeError(
            "installed packages do not match the frozen rllib-core profile"
        )
    started = monotonic()
    ray.init(
        num_cpus=cast(int, request["ray_num_cpus"]),
        include_dashboard=False,
        log_to_driver=False,
    )
    algorithm = None
    try:
        config = (
            PPOConfig()
            .api_stack(
                enable_rl_module_and_learner=True,
                enable_env_runner_and_connector_v2=True,
            )
            .environment(cast(str, request["environment_id"]))
            .framework(cast(str, request["framework"]))
            .debugging(seed=cast(int, request["seed"]), log_level="ERROR")
            .env_runners(
                num_env_runners=cast(int, request["num_env_runners"]),
                num_envs_per_env_runner=cast(int, request["num_envs_per_env_runner"]),
            )
            .learners(
                num_learners=cast(int, request["num_learners"]),
                num_gpus_per_learner=cast(int, request["num_gpus_per_learner"]),
            )
            .training(
                train_batch_size_per_learner=cast(
                    int, request["train_batch_size_per_learner"]
                ),
                minibatch_size=cast(int, request["minibatch_size"]),
                num_epochs=cast(int, request["num_epochs"]),
            )
            .evaluation(
                evaluation_interval=1,
                evaluation_duration=cast(int, request["evaluation_episodes"]),
                evaluation_duration_unit="episodes",
                evaluation_num_env_runners=0,
                evaluation_config={
                    "explore": cast(bool, request["evaluation_explore"])
                },
            )
        )
        algorithm = config.build_algo()
        result = cast(dict[str, object], algorithm.train())
        training = _metrics_group(result)
        evaluation_raw = result.get("evaluation")
        if not isinstance(evaluation_raw, Mapping):
            raise TypeError("RLlib train result omitted configured evaluation")
        evaluation = _metrics_group(cast(Mapping[str, object], evaluation_raw))
        step_candidates = (
            result.get("num_env_steps_sampled_lifetime"),
            training.get("num_env_steps_sampled_lifetime"),
            training.get("num_env_steps_sampled"),
        )
        steps_value = 0
        for candidate in step_candidates:
            try:
                steps_value = operator.index(cast(Any, candidate))
            except TypeError:
                continue
            if steps_value > 0:
                break
        if steps_value < 1:
            raise ValueError("RLlib sampled-step metric is missing or invalid")
        return {
            "schema_version": "automarkov.engineering-pilot-worker-response.v1",
            "pilot_id": "cartpole_cpu_smoke_v1",
            "status": "success",
            "package_versions": {
                **package_versions,
            },
            "python_version": python_version,
            "training": {
                "iterations_completed": 1,
                "num_env_steps_sampled_lifetime": steps_value,
                "episode_return_mean_decimal": (
                    _decimal_string(training["episode_return_mean"])
                    if training.get("episode_return_mean") is not None
                    else None
                ),
                "episode_length_mean_decimal": (
                    _decimal_string(training["episode_len_mean"])
                    if training.get("episode_len_mean") is not None
                    else None
                ),
            },
            "evaluation": {
                "episodes_completed": 1,
                "episode_return_mean_decimal": _decimal_string(
                    evaluation["episode_return_mean"]
                ),
                "episode_length_mean_decimal": _decimal_string(
                    evaluation["episode_len_mean"]
                ),
            },
            "reason_code": None,
            "error_type": None,
            "error_message": None,
        }
    finally:
        if algorithm is not None:
            algorithm.stop()
        ray.shutdown()
        if monotonic() - started > cast(int, request["wall_time_seconds"]):
            raise TimeoutError("worker exceeded the closed wall-time budget")


WorkerTrainer = Callable[[dict[str, object]], dict[str, object]]


def main(
    argv: Sequence[str] | None = None,
    *,
    trainer: WorkerTrainer = _run_rllib,
) -> int:
    args = _parser().parse_args(argv)
    request = _load_request(args.request)
    try:
        response = trainer(request)
        if (
            response.get("status") != "success"
            or response.get("python_version") != "3.11.13"
        ):
            raise RuntimeError("trainer returned a non-success response")
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - worker boundary closes arbitrary failures
        response = {
            "schema_version": "automarkov.engineering-pilot-worker-response.v1",
            "pilot_id": "cartpole_cpu_smoke_v1",
            "status": "terminal_failure",
            "package_versions": {
                "gymnasium": "1.2.2",
                "ray": "2.56.1",
                "torch": "2.13.0",
            },
            "python_version": platform.python_version(),
            "training": None,
            "evaluation": None,
            "reason_code": "worker_execution_failed",
            "error_type": type(error).__name__,
            "error_message": str(error)[:1024],
        }
        exit_code = 4
    args.result.write_text(
        json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
