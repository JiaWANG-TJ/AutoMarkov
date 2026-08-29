"""证据网关适配器：ScriptedEvidenceGateway 和 ProductionTavilyGateway。"""

from __future__ import annotations

from typing import Never

from automarkov.adapters.tavily_gateway import (  # noqa: F401
    ProductionTavilyGateway,
)
from automarkov.contracts.evidence import (
    CrawlEvidenceRequest,
    EvidenceGatewayResult,
    ExtractEvidenceRequest,
    SearchEvidenceRequest,
)
from automarkov.domain.errors import CapabilityDeferredError
from automarkov.public import AuthenticatedCommandContext


def _deferred(capability: str, owner_ticket: str) -> Never:
    raise CapabilityDeferredError(capability, owner_ticket)


class ScriptedEvidenceGateway:
    """脚本化证据网关桩，所有方法延迟到实现就绪。"""

    def search(
        self,
        request: SearchEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        _deferred("evidence.search", "T10")

    def extract(
        self,
        request: ExtractEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        _deferred("evidence.extract", "T10")

    def crawl(
        self,
        request: CrawlEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult:
        _deferred("evidence.crawl", "T10")
