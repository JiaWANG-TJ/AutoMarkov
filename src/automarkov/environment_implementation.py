from __future__ import annotations

from base64 import urlsafe_b64decode
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from automarkov.contracts.environment import (
    FrozenImplementationCatalog,
    ImplementationPlan,
    SignedRouteRequest,
)


def select_implementation_route(
    request: SignedRouteRequest,
    *,
    catalog: FrozenImplementationCatalog,
    trusted_suite_keys: Mapping[str, Ed25519PublicKey],
) -> ImplementationPlan:
    """仅从已签名 suite 输入与冻结 catalog 选择唯一实现路线。"""

    if type(request) is not SignedRouteRequest:
        raise ValueError("route request must use the exact validated type")
    if type(catalog) is not FrozenImplementationCatalog:
        raise ValueError("implementation catalog must use the exact validated type")
    if type(trusted_suite_keys) is not dict:
        raise ValueError("trusted suite keys must be a frozen caller snapshot")
    key = trusted_suite_keys.get(request.signing_key_id)
    if not isinstance(key, Ed25519PublicKey) or not request.signature_b64url:
        raise ValueError("route request signature is unavailable")
    try:
        signature = urlsafe_b64decode(
            request.signature_b64url + "=" * (-len(request.signature_b64url) % 4)
        )
        key.verify(signature, request.signing_bytes())
    except (InvalidSignature, ValueError):
        raise ValueError("route request signature is invalid") from None
    if request.implementation_catalog_hash != catalog.catalog_hash:
        raise ValueError("signed implementation catalog identity does not match")

    candidates = tuple(
        candidate
        for candidate in catalog.candidates
        if candidate.suite_id == request.suite_id
        and candidate.route == request.required_route
    )
    if len(candidates) != 1:
        raise ValueError(
            "signed required route does not resolve to exactly one candidate"
        )
    candidate = candidates[0]
    return ImplementationPlan(
        schema_version="automarkov.implementation-plan.v1",
        route_request_id=request.request_id,
        suite_id=request.suite_id,
        task_contract=request.task_contract,
        decision_process_spec=request.decision_process_spec,
        classification_result=request.classification_result,
        signed_suite_manifest=request.signed_suite_manifest,
        implementation_catalog_hash=request.implementation_catalog_hash,
        route=candidate.route,
        candidate_id=candidate.candidate_id,
        environment_id=candidate.environment_id,
        backend=candidate.backend,
        runtime_profile_id=candidate.runtime_profile_id,
        wrappers=candidate.wrappers,
        official_provenance=candidate.official_provenance,
    )


__all__ = ["select_implementation_route"]
