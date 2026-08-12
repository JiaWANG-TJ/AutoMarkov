from __future__ import annotations

from base64 import urlsafe_b64decode
from collections.abc import Mapping
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from automarkov.domain import (
    EvidenceCapabilityGrant,
    EvidenceStoreRef,
    EvidenceTier,
    GenerationEvidenceView,
    PrincipalKind,
    validate_strict_frozen_payload,
)
from automarkov.errors import EvidenceCapabilityDeniedError


class EvidenceAccessController:
    """只判定预注册能力，不解析或返回存储位置。"""

    def __init__(
        self,
        *,
        authenticated_principal_id: str,
        trusted_issuer_keys: Mapping[str, Ed25519PublicKey],
        registered_stores: Mapping[str, EvidenceStoreRef],
    ) -> None:
        if type(authenticated_principal_id) is not str:
            raise ValueError("authenticated principal identity must be exact text")
        if type(trusted_issuer_keys) is not dict or not trusted_issuer_keys:
            raise ValueError("evidence controller requires trusted issuer keys")
        if type(registered_stores) is not dict or not registered_stores:
            raise ValueError("evidence controller requires registered stores")
        self._authenticated_principal_id = authenticated_principal_id
        self._trusted_issuer_keys = MappingProxyType(dict(trusted_issuer_keys))
        stores: dict[str, EvidenceStoreRef] = {}
        for store_id, store in registered_stores.items():
            if type(store_id) is not str or type(store) is not EvidenceStoreRef:
                raise ValueError("registered evidence store has an invalid type")
            validated = validate_strict_frozen_payload(
                EvidenceStoreRef,
                store.model_dump(mode="json", round_trip=True, warnings="error"),
            )
            stores[store_id] = validated
        self._registered_stores = MappingProxyType(stores)

    def authorize(
        self,
        grant: EvidenceCapabilityGrant,
        store: EvidenceStoreRef,
    ) -> EvidenceStoreRef:
        if type(grant) is not EvidenceCapabilityGrant:
            raise ValueError("evidence grant must use the exact validated type")
        if type(store) is not EvidenceStoreRef:
            raise ValueError("evidence store must use the exact validated type")
        if not grant.has_validated_provenance():
            raise EvidenceCapabilityDeniedError(grant.principal_id, store.store_id)
        grant = EvidenceCapabilityGrant.model_validate(grant, strict=True)
        store = validate_strict_frozen_payload(
            EvidenceStoreRef,
            store.model_dump(mode="json", round_trip=True, warnings="error"),
        )
        trusted_store = self._registered_stores.get(store.store_id)
        key = self._trusted_issuer_keys.get(grant.issuer_key_id)
        if key is None:
            raise EvidenceCapabilityDeniedError(grant.principal_id, store.store_id)
        try:
            key.verify(urlsafe_b64decode(grant.signature + "=="), grant.signing_bytes())
        except InvalidSignature:
            raise EvidenceCapabilityDeniedError(
                grant.principal_id, store.store_id
            ) from None
        if (
            grant.principal_id != self._authenticated_principal_id
            or trusted_store != store
            or store.tier not in grant.tiers
            or store.store_id not in grant.store_ids
            or grant.store_identity_hashes[store.store_id] != store.identity_hash
        ):
            raise EvidenceCapabilityDeniedError(grant.principal_id, store.store_id)
        if trusted_store is None:  # pragma: no cover - 上述闭合条件已完成收窄
            raise EvidenceCapabilityDeniedError(grant.principal_id, store.store_id)
        return trusted_store

    def issue_generation_view(
        self,
        grant: EvidenceCapabilityGrant,
        stores: tuple[EvidenceStoreRef, ...],
    ) -> GenerationEvidenceView:
        """签发只含当前 principal 获准 generation evidence 的可信视图。"""

        if type(stores) is not tuple or not stores:
            raise ValueError("generation evidence stores must be a nonempty tuple")
        authorized_stores = tuple(self.authorize(grant, store) for store in stores)
        view = validate_strict_frozen_payload(
            GenerationEvidenceView,
            {
                "schema_version": "automarkov.generation-evidence-view.v1",
                "principal_id": self._authenticated_principal_id,
                "capability_grant": grant.model_dump(
                    mode="json", round_trip=True, warnings="error"
                ),
                "stores": [
                    store.model_dump(mode="json", round_trip=True, warnings="error")
                    for store in authorized_stores
                ],
            },
        )
        return self.verify_generation_view(view)

    def verify_generation_view(
        self,
        view: GenerationEvidenceView,
    ) -> GenerationEvidenceView:
        """按当前 principal、可信 issuer 与注册 store 重验 generation view。"""

        if (
            type(view) is not GenerationEvidenceView
            or not view.has_validated_provenance()
            or not view.capability_grant.has_validated_provenance()
        ):
            raise EvidenceCapabilityDeniedError(
                self._authenticated_principal_id,
                "store_generation_view",
            )
        if view.principal_id != self._authenticated_principal_id:
            raise EvidenceCapabilityDeniedError(
                view.principal_id,
                view.stores[0].store_id,
            )
        authorized_stores = tuple(
            self.authorize(view.capability_grant, store) for store in view.stores
        )
        return validate_strict_frozen_payload(
            GenerationEvidenceView,
            {
                "schema_version": view.schema_version,
                "principal_id": view.principal_id,
                "capability_grant": view.capability_grant.model_dump(
                    mode="json", round_trip=True, warnings="error"
                ),
                "stores": [
                    store.model_dump(mode="json", round_trip=True, warnings="error")
                    for store in authorized_stores
                ],
            },
        )


def validate_evidence_grant_payload(value: object) -> EvidenceCapabilityGrant:
    return validate_strict_frozen_payload(EvidenceCapabilityGrant, value)


__all__ = [
    "EvidenceAccessController",
    "EvidenceCapabilityDeniedError",
    "EvidenceCapabilityGrant",
    "EvidenceStoreRef",
    "EvidenceTier",
    "GenerationEvidenceView",
    "PrincipalKind",
    "validate_evidence_grant_payload",
]
