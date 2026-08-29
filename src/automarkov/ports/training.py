"""训练运行器端口协议。"""

from typing import Protocol, runtime_checkable

from automarkov.public import (
    PolicyEvaluationRequest,
    PolicyEvaluationResult,
    TrainingRequest,
    TrainingResult,
)


@runtime_checkable
class TrainingRunner(Protocol):
    def train(self, request: TrainingRequest) -> TrainingResult: ...
    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult: ...
