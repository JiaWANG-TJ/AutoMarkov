"""沙箱适配器：ScriptedExecutionSandbox 和 FixedCommitExecutionSandbox。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Never

from automarkov.domain.errors import CapabilityDeferredError
from automarkov.domain.models import ArtifactId
from automarkov.fixed_commit_runner import (
    FixedCommitExecutionRequest,
    FixedCommitRunner,
)
from automarkov.public import (
    ExecutionResult,
    FixedCommitJobRequest,
    SandboxRunRequest,
    SandboxTestRequest,
)


def _deferred(capability: str, owner_ticket: str) -> Never:
    raise CapabilityDeferredError(capability, owner_ticket)


class ScriptedExecutionSandbox:
    """脚本化沙箱执行适配器，通过可注入 run_handler 驱动。"""

    def __init__(
        self,
        *,
        run_handler: Callable[[SandboxRunRequest], ExecutionResult] | None = None,
    ) -> None:
        self._run_handler = run_handler

    def run(self, request: SandboxRunRequest) -> ExecutionResult:
        if self._run_handler is None:
            raise ValueError("sandbox run requires a configured deep implementation")
        return self._run_handler(request)

    def test(self, request: SandboxTestRequest) -> ExecutionResult:
        _deferred("sandbox.test", "T15")

    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult:
        raise ValueError("fixed-commit execution requires FixedCommitExecutionSandbox")


class FixedCommitExecutionSandbox:
    """固定提交沙箱适配器，仅支持 run_at_commit 方法。"""

    def __init__(self, runner: FixedCommitRunner) -> None:
        self._runner = runner

    def run(self, request: SandboxRunRequest) -> ExecutionResult:
        raise ValueError("fixed-commit adapter only supports run_at_commit")

    def test(self, request: SandboxTestRequest) -> ExecutionResult:
        raise ValueError("fixed-commit adapter only supports run_at_commit")

    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult:
        if request.schema_version != "automarkov.fixed-commit-job-request.v2":
            raise ValueError("fixed-commit execution requires request schema v2")
        specified_event_head = request.specified_event_head
        job_manifest = request.job_manifest
        if specified_event_head is None or job_manifest is None:
            raise ValueError("fixed-commit request v2 reference binding is incomplete")
        result = self._runner.run_at_commit(
            FixedCommitExecutionRequest(
                schema_version="automarkov.fixed-commit-execution-request.v1",
                specified_event_head=specified_event_head,
                job_manifest=job_manifest,
            )
        )
        return ExecutionResult(
            schema_version="automarkov.execution-result.v2",
            terminal_record_artifact_id=ArtifactId(
                root=result.process_terminal_record.artifact_id
            ),
            process_terminal_record=result.process_terminal_record,
            execution_attestation=result.execution_attestation,
            terminal_result=result.terminal_result,
        )
