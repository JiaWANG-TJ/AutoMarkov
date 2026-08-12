from __future__ import annotations

from base64 import urlsafe_b64encode

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from automarkov.evidence_access import (
    EvidenceAccessController,
    EvidenceCapabilityDeniedError,
    EvidenceCapabilityGrant,
    EvidenceStoreRef,
    GenerationEvidenceView,
    validate_evidence_grant_payload,
)

_ISSUER_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)


def _store(tier: str, suffix: str) -> EvidenceStoreRef:
    return EvidenceStoreRef.model_validate(
        {
            "schema_version": "automarkov.evidence-store-ref.v1",
            "store_id": f"store_{suffix}",
            "tier": tier,
            "identity_hash": "sha256:" + "a" * 64,
        },
        strict=True,
    )


def _grant(kind: str, tiers: tuple[str, ...]) -> EvidenceCapabilityGrant:
    store_suffixes = {
        "allowed_evidence": "allowed",
        "public_dev": "public",
        "sealed_gold": "sealed",
    }
    store_ids = tuple(f"store_{store_suffixes[tier]}" for tier in tiers)
    payload: dict[str, object] = {
        "schema_version": "automarkov.evidence-capability-grant.v1",
        "signing_domain": "AutoMarkov-Evidence-Capability-Grant-v1",
        "capability_id": "capability_contract",
        "principal_id": "principal_contract",
        "principal_kind": kind,
        "tiers": list(tiers),
        "store_ids": list(store_ids),
        "store_identity_hashes": {
            store_id: "sha256:" + "a" * 64 for store_id in store_ids
        },
        "issuer_key_id": "key_evidence_issuer",
        "nonce": urlsafe_b64encode(b"n" * 32).decode().rstrip("="),
        "signature_algorithm": "Ed25519",
        "signature": urlsafe_b64encode(b"\x00" * 64).decode().rstrip("="),
    }
    unsigned = EvidenceCapabilityGrant.model_validate(payload, strict=True)
    payload["signature"] = (
        urlsafe_b64encode(_ISSUER_PRIVATE_KEY.sign(unsigned.signing_bytes()))
        .decode()
        .rstrip("=")
    )
    return validate_evidence_grant_payload(payload)


def _controller(*stores: EvidenceStoreRef) -> EvidenceAccessController:
    return EvidenceAccessController(
        authenticated_principal_id="principal_contract",
        trusted_issuer_keys={"key_evidence_issuer": _ISSUER_PRIVATE_KEY.public_key()},
        registered_stores={store.store_id: store for store in stores},
    )


@pytest.mark.parametrize(
    ("principal_kind", "tiers"),
    [
        ("researcher", ("allowed_evidence",)),
        ("text_agent", ("allowed_evidence",)),
        ("formal_agent", ("allowed_evidence",)),
        ("developer", ("allowed_evidence", "public_dev")),
        ("unit_tester", ("allowed_evidence", "public_dev")),
        ("simulation_tester", ("allowed_evidence", "public_dev")),
        ("training_analyst", ("public_dev",)),
        ("training_runner", ("public_dev",)),
        ("sealed_evaluator", ("sealed_gold",)),
    ],
)
def test_principal_capability_matrix_is_closed(
    principal_kind: str,
    tiers: tuple[str, ...],
) -> None:
    assert _grant(principal_kind, tiers).tiers == tiers


@pytest.mark.parametrize(
    ("principal_kind", "forbidden_tier"),
    [
        ("researcher", "public_dev"),
        ("text_agent", "public_dev"),
        ("formal_agent", "public_dev"),
        ("developer", "sealed_gold"),
        ("unit_tester", "sealed_gold"),
        ("simulation_tester", "sealed_gold"),
        ("training_analyst", "allowed_evidence"),
        ("training_analyst", "sealed_gold"),
        ("training_runner", "allowed_evidence"),
        ("training_runner", "sealed_gold"),
        ("sealed_evaluator", "allowed_evidence"),
        ("sealed_evaluator", "public_dev"),
    ],
)
def test_principals_cannot_self_grant_cross_tier_capabilities(
    principal_kind: str,
    forbidden_tier: str,
) -> None:
    with pytest.raises(ValidationError):
        _grant(principal_kind, (forbidden_tier,))


def test_generation_view_requires_controller_issuance_for_registered_evidence() -> None:
    allowed = _store("allowed_evidence", "allowed")
    grant = _grant("researcher", ("allowed_evidence",))
    controller = _controller(allowed)

    view = controller.issue_generation_view(grant, (allowed,))

    assert view.principal_id == grant.principal_id
    assert view.capability_grant == grant
    assert tuple(store.tier for store in view.stores) == ("allowed_evidence",)
    assert view.has_validated_provenance()
    assert view.capability_grant.has_validated_provenance()
    assert controller.verify_generation_view(view) == view

    sealed_registration = EvidenceStoreRef(
        schema_version="automarkov.evidence-store-ref.v1",
        store_id=allowed.store_id,
        tier="sealed_gold",
        identity_hash=allowed.identity_hash,
    )
    with pytest.raises(EvidenceCapabilityDeniedError):
        _controller(sealed_registration).issue_generation_view(grant, (allowed,))

    self_asserted = GenerationEvidenceView(
        schema_version="automarkov.generation-evidence-view.v1",
        principal_id=grant.principal_id,
        capability_grant=grant,
        stores=(allowed,),
    )
    with pytest.raises(EvidenceCapabilityDeniedError):
        controller.verify_generation_view(self_asserted)

    for forbidden in ("public_dev", "sealed_gold"):
        with pytest.raises(ValidationError):
            GenerationEvidenceView(
                schema_version="automarkov.generation-evidence-view.v1",
                principal_id=grant.principal_id,
                capability_grant=grant,
                stores=(allowed, _store(forbidden, "forbidden")),
            )


def test_generation_view_allows_authorized_public_dev_without_sealed_gold() -> None:
    allowed = _store("allowed_evidence", "allowed")
    public = _store("public_dev", "public")
    grant = _grant("developer", ("allowed_evidence", "public_dev"))
    controller = _controller(allowed, public)

    view = controller.issue_generation_view(grant, (allowed, public))

    assert tuple(store.tier for store in view.stores) == (
        "allowed_evidence",
        "public_dev",
    )
    assert controller.verify_generation_view(view) == view
    with pytest.raises(ValidationError):
        GenerationEvidenceView(
            schema_version="automarkov.generation-evidence-view.v1",
            principal_id=grant.principal_id,
            capability_grant=grant,
            stores=(allowed, public, _store("sealed_gold", "sealed")),
        )


def test_training_analyst_has_a_dedicated_public_dev_generation_principal() -> None:
    public = _store("public_dev", "public")
    grant = _grant("training_analyst", ("public_dev",))
    controller = _controller(public)

    view = controller.issue_generation_view(grant, (public,))

    assert view.capability_grant.principal_kind == "training_analyst"
    assert tuple(store.tier for store in view.stores) == ("public_dev",)
    assert controller.verify_generation_view(view) == view


def test_controller_matches_exact_capability_principal_and_store() -> None:
    allowed = _store("allowed_evidence", "allowed")
    public = _store("public_dev", "public")
    controller = _controller(allowed, public)
    grant = _grant("researcher", ("allowed_evidence",))

    authorized = controller.authorize(grant, allowed)
    assert authorized.model_dump(mode="json") == allowed.model_dump(mode="json")
    assert authorized.has_validated_provenance()
    with pytest.raises(EvidenceCapabilityDeniedError) as raised:
        controller.authorize(grant, _store("public_dev", "public"))

    assert raised.value.code == "evidence_capability_denied"
    assert "path" not in str(raised.value).lower()


def test_capability_is_bound_to_the_exact_registered_store() -> None:
    allowed = _store("allowed_evidence", "allowed")
    other = _store("allowed_evidence", "other")
    controller = _controller(allowed, other)
    grant = _grant("researcher", ("allowed_evidence",))

    with pytest.raises(EvidenceCapabilityDeniedError):
        controller.authorize(grant, other)

    drifted = EvidenceStoreRef(
        schema_version="automarkov.evidence-store-ref.v1",
        store_id="store_allowed",
        tier="allowed_evidence",
        identity_hash="sha256:" + "b" * 64,
    )
    with pytest.raises(EvidenceCapabilityDeniedError):
        controller.authorize(grant, drifted)


def test_self_asserted_or_wrong_principal_grants_are_rejected() -> None:
    allowed = _store("allowed_evidence", "allowed")
    wrong_principal_controller = EvidenceAccessController(
        authenticated_principal_id="principal_other",
        trusted_issuer_keys={"key_evidence_issuer": _ISSUER_PRIVATE_KEY.public_key()},
        registered_stores={allowed.store_id: allowed},
    )

    with pytest.raises(EvidenceCapabilityDeniedError):
        wrong_principal_controller.authorize(
            _grant("researcher", ("allowed_evidence",)),
            allowed,
        )

    self_asserted = EvidenceCapabilityGrant.model_validate(
        _grant("researcher", ("allowed_evidence",)).model_dump(mode="json"),
        strict=True,
    )
    with pytest.raises(EvidenceCapabilityDeniedError):
        _controller(allowed).authorize(self_asserted, allowed)


def test_capability_nonce_rejects_noncanonical_base64url_alias() -> None:
    payload = _grant("researcher", ("allowed_evidence",)).model_dump(mode="json")
    payload["nonce"] = "A" * 42 + "B"

    with pytest.raises(ValidationError, match="canonical 32-byte"):
        EvidenceCapabilityGrant.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    "forbidden_field",
    ["path", "locator", "credential", "secret", "gold_artifact_id"],
)
def test_store_refs_cannot_carry_locators_or_sealed_identity(
    forbidden_field: str,
) -> None:
    payload = {
        "schema_version": "automarkov.evidence-store-ref.v1",
        "store_id": "store_public",
        "tier": "allowed_evidence",
        "identity_hash": "sha256:" + "a" * 64,
        forbidden_field: "forbidden",
    }

    with pytest.raises(ValidationError):
        EvidenceStoreRef.model_validate(payload, strict=True)
