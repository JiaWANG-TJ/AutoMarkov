from __future__ import annotations

from automarkov.adapters import InMemoryCompiler
from automarkov.domain import RunView, TaskRequest
from automarkov.public import Compiler


def compile_task(request: TaskRequest, *, compiler: Compiler | None = None) -> RunView:
    active_compiler = InMemoryCompiler() if compiler is None else compiler
    return active_compiler.resume(active_compiler.start(request))
