"""环境绑定端口协议。"""

from typing import Protocol, runtime_checkable

from automarkov.ports.remote_env import RemoteEnv
from automarkov.public import EnvironmentRef, RuntimeProfileRef


@runtime_checkable
class EnvironmentBinding(Protocol):
    def bind(
        self, profile: RuntimeProfileRef, env_ref: EnvironmentRef
    ) -> RemoteEnv: ...
