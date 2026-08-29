"""远程环境端口协议。"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class RemoteEnv(Protocol):
    def exchange(self, canonical_frame: bytes) -> bytes: ...
