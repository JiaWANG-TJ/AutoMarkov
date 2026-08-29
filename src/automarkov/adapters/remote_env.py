"""远程环境适配器：ScriptedRemoteEnv。"""

from __future__ import annotations

from typing import Never

from automarkov.domain.errors import CapabilityDeferredError
from automarkov.public import CloseResult


def _deferred(capability: str, owner_ticket: str) -> Never:
    raise CapabilityDeferredError(capability, owner_ticket)


class ScriptedRemoteEnv:
    """脚本化远程环境桩，exchange 和 close 延迟到实现就绪。"""

    def exchange(self, canonical_frame: bytes) -> bytes:
        _deferred("remote_env.exchange", "T12")

    def close(self) -> CloseResult:
        _deferred("remote_env.close", "T12")
