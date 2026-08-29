from __future__ import annotations

from automarkov.adapters import InMemoryCompiler
from automarkov.domain.models import RunId, TaskRequest
from automarkov.public import Compiler


def compile_task(request: TaskRequest, *, compiler: Compiler | None = None) -> RunId:
    active_compiler = InMemoryCompiler() if compiler is None else compiler
    return active_compiler.start(request)
