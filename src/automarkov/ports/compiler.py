"""Compiler 端口协议。"""

from typing import Protocol, runtime_checkable

from automarkov.domain.models import RunId, TaskRequest, VerifiedEventHead
from automarkov.lifecycle import LifecycleCommitResult, RunProjection
from automarkov.public import LifecycleCommandInput, PackageResult


@runtime_checkable
class Compiler(Protocol):
    def start(self, request: TaskRequest) -> RunId: ...
    def dispatch(self, request: LifecycleCommandInput) -> LifecycleCommitResult: ...
    def resume(self, run_id: RunId, head: VerifiedEventHead) -> RunProjection: ...
    def package(self, run_id: RunId, head: VerifiedEventHead) -> PackageResult: ...
