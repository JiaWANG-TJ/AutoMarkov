from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.environment_contracts import (
    EnvironmentCandidate,
    FrozenImplementationCatalog,
    ImplementationRoute,
    SignedRouteRequest,
)
from automarkov.environment_implementation import select_implementation_route
from automarkov.lifecycle import ArtifactReference


def _ref(name: str, digit: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"artifact_{sha256(name.encode()).hexdigest()}",
        payload_hash=f"sha256:{digit * 64}",
    )


def _signed_request(
    private_key: Ed25519PrivateKey,
    *,
    required_route: ImplementationRoute = "reuse",
) -> SignedRouteRequest:
    unsigned = SignedRouteRequest(
        schema_version="automarkov.signed-route-request.v1",
        signing_domain="AutoMarkov-Signed-Route-Request-v1",
        request_id="route_request_cartpole",
        suite_id="suite_cartpole",
        task_contract=_ref("task", "1"),
        decision_process_spec=_ref("spec", "2"),
        classification_result=_ref("classification", "3"),
        signed_suite_manifest=_ref("suite", "4"),
        implementation_catalog_hash="sha256:" + "a" * 64,
        required_route=required_route,
        issued_at="2026-08-12T12:00:00Z",
        nonce_b64url=urlsafe_b64encode(b"n" * 32).decode().rstrip("="),
        signing_key_id="key_suite_owner",
        signature_b64url="",
    )
    signature = private_key.sign(unsigned.signing_bytes())
    return unsigned.model_copy(
        update={"signature_b64url": urlsafe_b64encode(signature).decode().rstrip("=")}
    )


def _catalog() -> FrozenImplementationCatalog:
    return FrozenImplementationCatalog(
        schema_version="automarkov.frozen-implementation-catalog.v1",
        catalog_id="catalog_cartpole_v1",
        candidates=(
            EnvironmentCandidate(
                candidate_id="candidate_gymnasium_cartpole_v1",
                route="reuse",
                suite_id="suite_cartpole",
                environment_id="CartPole-v1",
                backend="gymnasium",
                package_name="gymnasium",
                package_version="1.2.2",
                upstream_commit="a923da5d4415a1aa5195d99341069da5e16deed7",
                distribution_hash=(
                    "sha256:f04ec362b1fdf73a8b327db5ef89384a3f2ba411e05d3521513414fbbb2199c8"
                ),
                runtime_profile_id="rllib-core",
                wrappers=(),
                evidence_ids=("E-gymnasium-cartpole-v1.2.2",),
                official_provenance=_ref("gymnasium_provenance", "5"),
            ),
        ),
        catalog_hash="sha256:" + "a" * 64,
    )


def test_signed_suite_request_selects_the_only_frozen_cartpole_reuse_candidate() -> (
    None
):
    private_key = Ed25519PrivateKey.generate()

    plan = select_implementation_route(
        _signed_request(private_key),
        catalog=_catalog(),
        trusted_suite_keys={"key_suite_owner": private_key.public_key()},
    )

    assert plan.route == "reuse"
    assert plan.candidate_id == "candidate_gymnasium_cartpole_v1"
    assert plan.environment_id == "CartPole-v1"
    assert plan.runtime_profile_id == "rllib-core"
    assert plan.wrappers == ()
    assert plan.official_provenance == _ref("gymnasium_provenance", "5")


def test_route_selector_rejects_signature_or_catalog_route_substitution() -> None:
    trusted = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()

    with pytest.raises(ValueError, match="signature"):
        select_implementation_route(
            _signed_request(attacker),
            catalog=_catalog(),
            trusted_suite_keys={"key_suite_owner": trusted.public_key()},
        )

    with pytest.raises(ValueError, match="required route"):
        select_implementation_route(
            _signed_request(trusted, required_route="generate"),
            catalog=_catalog(),
            trusted_suite_keys={"key_suite_owner": trusted.public_key()},
        )


def test_catalog_is_deeply_frozen_and_selector_has_no_route_override() -> None:
    private_key = Ed25519PrivateKey.generate()
    catalog = _catalog()

    with pytest.raises(ValidationError):
        catalog.candidates[0].route = "generate"  # type: ignore[misc]

    with pytest.raises(TypeError):
        select_implementation_route(
            _signed_request(private_key),
            catalog=catalog,
            trusted_suite_keys={"key_suite_owner": private_key.public_key()},
            route_override="generate",  # type: ignore[call-arg]
        )
