"""LLM 运行时适配器：ScriptedLocalLlmRuntime（桩）与 ProductionVllmRuntime（生产级）。"""

from __future__ import annotations

from typing import Never

from automarkov.adapters.llm_vllm import ProductionVllmRuntime  # noqa: F401
from automarkov.contracts.llm import (
    LlmCompletionRequest,
    LlmCompletionResult,
    LlmProbeResult,
    LlmStartRequest,
)
from automarkov.domain.errors import CapabilityDeferredError
from automarkov.public import CloseResult


def _deferred(capability: str, owner_ticket: str) -> Never:
    raise CapabilityDeferredError(capability, owner_ticket)


class ScriptedLocalLlmRuntime:
    """脚本化 LLM 运行时桩，所有方法延迟到实现就绪。"""

    def start(self, request: LlmStartRequest) -> LlmProbeResult:
        _deferred("llm.start", "T05")

    def probe(self) -> LlmProbeResult:
        _deferred("llm.probe", "T05")

    def complete(self, request: LlmCompletionRequest) -> LlmCompletionResult:
        _deferred("llm.complete", "T05")

    def close(self) -> CloseResult:
        _deferred("llm.close", "T05")
