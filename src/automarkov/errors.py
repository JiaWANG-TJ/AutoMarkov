from __future__ import annotations


class AutoMarkovError(RuntimeError):
    code = "automarkov_error"


class UnknownRunError(AutoMarkovError):
    code = "unknown_run"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"unknown run: {run_id}")


class RunIdCollisionError(AutoMarkovError):
    code = "run_id_collision"

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"run ID already exists: {run_id}")


class CapabilityDeferredError(AutoMarkovError):
    code = "capability_deferred"

    def __init__(self, capability: str, owner_ticket: str) -> None:
        self.capability = capability
        self.owner_ticket = owner_ticket
        super().__init__(f"{capability} is deferred to {owner_ticket}")
