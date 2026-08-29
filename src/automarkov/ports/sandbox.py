"""沙箱执行端口协议。"""

from typing import Protocol, runtime_checkable

from automarkov.public import (
    ExecutionResult,
    FixedCommitJobRequest,
    SandboxRunRequest,
    SandboxTestRequest,
)


@runtime_checkable
class ExecutionSandbox(Protocol):
    def run(self, request: SandboxRunRequest) -> ExecutionResult: ...
    def test(self, request: SandboxTestRequest) -> ExecutionResult: ...
    def run_at_commit(self, request: FixedCommitJobRequest) -> ExecutionResult: ...
