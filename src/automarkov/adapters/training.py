"""训练适配器：ScriptedTrainingRunner 桩 + RllibTrainingRunner 生产实现。"""

from __future__ import annotations

from typing import Never

from automarkov.domain.errors import CapabilityDeferredError
from automarkov.public import (
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
    TrainingRequest,
    TrainingResult,
)


def _deferred(capability: str, owner_ticket: str) -> Never:
    raise CapabilityDeferredError(capability, owner_ticket)


class ScriptedTrainingRunner:
    """脚本化训练运行器桩，train 和 evaluate 延迟到实现就绪。"""

    def train(self, request: TrainingRequest) -> TrainingResult:
        _deferred("training.train", "T18")

    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult:
        _deferred("training.evaluate", "T19")
