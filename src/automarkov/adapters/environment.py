"""环境绑定适配器：InMemoryEnvironmentBinding。"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from automarkov.adapters.remote_env import ScriptedRemoteEnv
from automarkov.public import EnvironmentRef, RuntimeProfileRef


class InMemoryEnvironmentBinding:
    """内存环境绑定适配器，通过可注入 bind_handler 驱动。"""

    def __init__(
        self,
        *,
        bind_handler: (
            Callable[[RuntimeProfileRef, EnvironmentRef], object] | None
        ) = None,
    ) -> None:
        self._bind_handler = bind_handler

    def bind(
        self, profile: RuntimeProfileRef, env_ref: EnvironmentRef
    ) -> ScriptedRemoteEnv:
        if self._bind_handler is None:
            raise ValueError(
                "environment bind requires a configured deep implementation"
            )
        return cast(ScriptedRemoteEnv, self._bind_handler(profile, env_ref))
