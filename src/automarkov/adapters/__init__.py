"""适配器层：所有端口协议的本地实现。"""

from automarkov.adapters.compiler import InMemoryCompiler as InMemoryCompiler
from automarkov.adapters.environment import (
    InMemoryEnvironmentBinding as InMemoryEnvironmentBinding,
)
from automarkov.adapters.evidence import (
    ScriptedEvidenceGateway as ScriptedEvidenceGateway,
)
from automarkov.adapters.llm import ScriptedLocalLlmRuntime as ScriptedLocalLlmRuntime
from automarkov.adapters.remote_env import ScriptedRemoteEnv as ScriptedRemoteEnv

# 仓库适配器从子包重导出
from automarkov.adapters.repository import (
    InMemoryArtifactRepository as InMemoryArtifactRepository,
)
from automarkov.adapters.repository import (
    SqliteArtifactRepository as SqliteArtifactRepository,
)
from automarkov.adapters.sandbox import (
    FixedCommitExecutionSandbox as FixedCommitExecutionSandbox,
)
from automarkov.adapters.sandbox import (
    ScriptedExecutionSandbox as ScriptedExecutionSandbox,
)
from automarkov.adapters.training import (
    ScriptedTrainingRunner as ScriptedTrainingRunner,
)
from automarkov.local_llm_runtime import (
    AttachedLocalLlmRuntime as AttachedLocalLlmRuntime,
)
from automarkov.local_llm_runtime import (
    PrivilegedUnixRuntimeConnectionProvider as PrivilegedUnixRuntimeConnectionProvider,
)

__all__ = [
    "AttachedLocalLlmRuntime",
    "FixedCommitExecutionSandbox",
    "InMemoryArtifactRepository",
    "InMemoryCompiler",
    "InMemoryEnvironmentBinding",
    "PrivilegedUnixRuntimeConnectionProvider",
    "ScriptedEvidenceGateway",
    "ScriptedExecutionSandbox",
    "ScriptedLocalLlmRuntime",
    "ScriptedRemoteEnv",
    "ScriptedTrainingRunner",
    "SqliteArtifactRepository",
]
