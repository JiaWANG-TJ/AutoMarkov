"""LLM 运行时端口协议。"""

from typing import Protocol, runtime_checkable

from automarkov.contracts.llm import (
    LlmCompletionRequest,
    LlmCompletionResult,
    LlmProbeResult,
    LlmStartRequest,
)
from automarkov.public import CloseResult


@runtime_checkable
class LocalLlmRuntime(Protocol):
    def start(self, request: LlmStartRequest) -> LlmProbeResult: ...
    def probe(self) -> LlmProbeResult: ...
    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult: ...
    def close(self) -> CloseResult: ...
