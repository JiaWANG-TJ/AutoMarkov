"""证据网关端口协议。"""

from typing import Protocol, runtime_checkable

from automarkov.contracts.evidence import (
    CrawlEvidenceRequest,
    EvidenceGatewayResult,
    ExtractEvidenceRequest,
    SearchEvidenceRequest,
)
from automarkov.public import AuthenticatedCommandContext


@runtime_checkable
class EvidenceGateway(Protocol):
    def search(
        self,
        request: SearchEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult: ...

    def extract(
        self,
        request: ExtractEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult: ...

    def crawl(
        self,
        request: CrawlEvidenceRequest,
        *,
        context: AuthenticatedCommandContext,
    ) -> EvidenceGatewayResult: ...
