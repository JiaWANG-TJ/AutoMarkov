"""端口层：纯 Protocol 定义，无实现依赖。"""

from automarkov.ports.compiler import Compiler as Compiler
from automarkov.ports.environment import EnvironmentBinding as EnvironmentBinding
from automarkov.ports.evidence import EvidenceGateway as EvidenceGateway
from automarkov.ports.llm import LocalLlmRuntime as LocalLlmRuntime
from automarkov.ports.remote_env import RemoteEnv as RemoteEnv
from automarkov.ports.sandbox import ExecutionSandbox as ExecutionSandbox
from automarkov.ports.training import TrainingRunner as TrainingRunner
