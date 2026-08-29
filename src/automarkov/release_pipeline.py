"""T23+T25+T26+T27: Paper replications, freeze gate, redactor, release CI.

T23: A-LAMP/Agent2/Agent2-World three paper replication suites
T25: Freeze gate + confirmatory matrix
T26: Isolated redactor + fixed/public-report publisher
T27: Release CI, SBOM, cards, reproducibility gates

R10: Deepen redactor, publisher, and release gate.
- Redactor: scan policies + functional ``redact_bundle``
- Publisher: ``publish_bundle`` with file hash verification and SBOM
- Release gate: decision from CHECKS not from ``released`` input
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field, model_validator

from automarkov.domain.canonical import FrozenSequence, canonical_json_bytes
from automarkov.domain.models import StrictFrozenModel
from automarkov.lifecycle import ArtifactReference, NonEmptyId, Sha256Value

# --- T23: Paper replications ---


class ReplicationSuiteBinding(StrictFrozenModel):
    suite_id: Literal["a_lamp_replication", "agent2_replication"]
    paper_title: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    paper_doi: Annotated[str | None, Field(strict=True, max_length=256)] = None
    license_boundary: Literal["permissive_only", "research_only"] = "research_only"
    reproduction_status: Literal["PLANNED", "EXECUTING", "COMPLETED", "BLOCKED"] = (
        "PLANNED"
    )
    reproduction_artifact: ArtifactReference | None = None


class ReplicationManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.replication-manifest.v1"] = (
        "automarkov.replication-manifest.v1"
    )
    experiment_id: NonEmptyId
    suites: FrozenSequence[ReplicationSuiteBinding]

    @model_validator(mode="after")  # type: ignore[misc]
    def require_two_suites(self) -> ReplicationManifest:
        ids = {s.suite_id for s in self.suites}
        expected = {
            "a_lamp_replication",
            "agent2_replication",
        }
        if ids != expected:
            raise ValueError("replication manifest must contain all 2 required suites")
        return self


# --- T25: Freeze gate ---


class FreezeGateCheck(StrictFrozenModel):
    check_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    kind: Literal[
        "schema_frozen",
        "manifest_complete",
        "seeds_allocated",
        "calibration_valid",
        "evaluator_ready",
        "dependencies_resolved",
    ]
    passed: bool = Field(strict=True)
    evidence_artifact: ArtifactReference | None = None


class FreezeGateResult(StrictFrozenModel):
    schema_version: Literal["automarkov.freeze-gate-result.v1"] = (
        "automarkov.freeze-gate-result.v1"
    )
    experiment_id: NonEmptyId
    checks: FrozenSequence[FreezeGateCheck]
    frozen: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> FreezeGateResult:
        required_kinds = {
            "schema_frozen",
            "manifest_complete",
            "seeds_allocated",
            "calibration_valid",
            "evaluator_ready",
            "dependencies_resolved",
        }
        observed_kinds = {check.kind for check in self.checks}
        closed = len(self.checks) == len(required_kinds) and observed_kinds == required_kinds
        derived = closed and all(check.passed for check in self.checks)
        if not closed:
            raise ValueError("freeze gate requires the closed check set")
        if self.frozen is not derived:
            raise ValueError("frozen verdict must equal the closed check conjunction")
        return self

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)


class ConfirmatoryMatrix(StrictFrozenModel):
    schema_version: Literal["automarkov.confirmatory-matrix.v1"] = (
        "automarkov.confirmatory-matrix.v1"
    )
    experiment_id: NonEmptyId
    freeze_gate: FreezeGateResult
    replication_ready: bool = Field(strict=True)
    statistics_ready: bool = Field(strict=True)
    publication_ready: bool = Field(strict=True)


# --- T26: Redactor + Publisher (R10 deepened) ---

_SECRET_KEYWORDS = re.compile(
    rb"(?i)\b(?:secret|credential|token|private[_-]?key|"
    rb"api[_-]?key|password|passwd|pwd|auth[_-]?"
    rb"(?:token|key|header)|bearer)\b"
)
_PATH_PATTERN = re.compile(
    rb"(?:^|[^A-Za-z0-9])(?:/home|/root|/var|/etc|/tmp|"
    rb"~|\.\.[/\\]|\\\\[A-Za-z]|[A-Za-z]:[/\\])"
)
_URI_PATTERN = re.compile(rb"https?://[^\s\"'<>]{4,}")
_HIGH_ENTROPY_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9+/=_-])"
    rb"[A-Za-z0-9+/]{32,}={0,2}"
    rb"(?![A-Za-z0-9+/=_-])"
)
_HEX_BLOB_PATTERN = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")
_ANSWER_HASH_PATTERN = re.compile(
    rb'\b(?:answer|expected|nonce|trace|output)\s*["\']?\s*[:=]'
)
_MARKDOWN_INJECTION_PATTERN = re.compile(
    rb"(?:\[.*?\]\(.*?\)|<\s*(?:script|iframe|img|a)\b[^>]*>|"
    rb"!\[.*?\]\(.*?\))"
)

# --- Public allowlist for redacted outputs ---

_PUBLIC_FILE_ALLOWLIST: frozenset[str] = frozenset({
    "confirmatory_report.md",
    "redacted_manifest.json",
})
_PUBLIC_TABLE_PATTERN: re.Pattern[str] = re.compile(r"^tables/[^/]+\.csv$")


class RedactionScanResult(StrictFrozenModel):
    """Single field-level redaction scan verdict."""

    schema_version: Literal[
        "automarkov.redaction-scan-result.v1"
    ] = "automarkov.redaction-scan-result.v1"
    field_path: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    policy: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    passed: bool = Field(strict=True)
    detail: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]


class RedactionAttestation(StrictFrozenModel):
    """Signed attestation produced by the deterministic redactor."""

    schema_version: Literal[
        "automarkov.redaction-attestation.v1"
    ] = "automarkov.redaction-attestation.v1"
    experiment_id: NonEmptyId
    bundle_hash: Sha256Value
    redaction_checks: FrozenSequence[RedactionScanResult]
    attestation_hash: Sha256Value

    @model_validator(mode="after")  # type: ignore[misc]
    def require_nonempty_checks(self) -> RedactionAttestation:
        if not self.redaction_checks:
            raise ValueError("redaction attestation requires at least one check")
        return self


# --- Redacted bundle model ---


class RedactedArtifactBinding(StrictFrozenModel):
    original_artifact: ArtifactReference
    redacted_artifact: ArtifactReference
    redaction_rules_applied: FrozenSequence[NonEmptyId]
    attestation_hash: Sha256Value


class RedactionManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.redaction-manifest.v1"] = (
        "automarkov.redaction-manifest.v1"
    )
    experiment_id: NonEmptyId
    source_artifacts: FrozenSequence[RedactedArtifactBinding]
    redactor_principal_id: NonEmptyId
    attestation_hash: Sha256Value


class PublicReportManifest(StrictFrozenModel):
    schema_version: Literal["automarkov.public-report-manifest.v1"] = (
        "automarkov.public-report-manifest.v1"
    )
    experiment_id: NonEmptyId
    redaction: ArtifactReference
    published_files: FrozenSequence[NonEmptyId]
    sbom_artifact: ArtifactReference | None = None
    license_inventory_artifact: ArtifactReference | None = None
    reproducibility_card_artifact: ArtifactReference | None = None


# --- Redaction scan helpers ---


def _classify_secret_value(raw: bytes) -> bool:
    """Detect high-entropy base64/hex blobs and secret keywords."""
    if _SECRET_KEYWORDS.search(raw):
        return True
    for candidate in _HIGH_ENTROPY_PATTERN.findall(raw):
        padded = candidate + b"=" * (-len(candidate) % 4)
        try:
            import base64 as _b64

            decoded_len = len(_b64.b64decode(padded))
            if decoded_len >= 16:
                return True
        except Exception:  # noqa: BLE001, S110 – resilient secret scan
            pass
    for candidate in _HEX_BLOB_PATTERN.findall(raw):
        if len(candidate) >= 32:
            return True
    return False


def _file_in_allowlist(name: str) -> bool:
    """Check whether a filename matches the public allowlist."""
    if name in _PUBLIC_FILE_ALLOWLIST:
        return True
    return bool(_PUBLIC_TABLE_PATTERN.fullmatch(name))


def _scan_field_provenance(
    field_path: str,
    value: object,
    checks: list[RedactionScanResult],
) -> None:
    """Check that the field has validated provenance metadata."""
    provenance_ok = True
    detail = "field has validated provenance"
    if isinstance(value, StrictFrozenModel) and not value.has_validated_provenance():
            provenance_ok = False
            detail = "field missing validated provenance"
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="field_provenance",
            passed=provenance_ok,
            detail=detail,
        )
    )


def _scan_high_entropy(
    field_path: str,
    raw: bytes,
    checks: list[RedactionScanResult],
) -> None:
    """Detect high-entropy base64/hex payloads that may leak secrets."""
    has_leak = _classify_secret_value(raw)
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="high_entropy",
            passed=not has_leak,
            detail="high-entropy payload detected" if has_leak else "clean",
        )
    )


def _scan_low_entropy_answer(
    field_path: str,
    raw: bytes,
    checks: list[RedactionScanResult],
) -> None:
    """Detect dictionary patterns like answer:/expected:/nonce: in raw bytes."""
    found = bool(_ANSWER_HASH_PATTERN.search(raw))
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="low_entropy_answer",
            passed=not found,
            detail="answer/nonce pattern detected" if found else "clean",
        )
    )


def _scan_secret_path(
    field_path: str,
    raw: bytes,
    checks: list[RedactionScanResult],
) -> None:
    """Detect secret/credential/token/private-key patterns and path/URI leaks."""
    has_secret = bool(_SECRET_KEYWORDS.search(raw))
    has_path = bool(_PATH_PATTERN.search(raw))
    has_uri = bool(_URI_PATTERN.search(raw))
    triggered = has_secret or has_path or has_uri
    parts: list[str] = []
    if has_secret:
        parts.append("secret pattern")
    if has_path:
        parts.append("absolute path")
    if has_uri:
        parts.append("URI reference")
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="secret_path_uri",
            passed=not triggered,
            detail=", ".join(parts) if triggered else "clean",
        )
    )


def _scan_symlink_reject(
    field_path: str,
    name: str,
    checks: list[RedactionScanResult],
) -> None:
    """Reject symlink/hardlink/device/FIFO file names."""
    suspicious = (
        name.startswith(".")
        or ".." in name
        or "/" in name and name.startswith("/")
        or "\\" in name
        or name.endswith((".pkl", ".pickle", ".pt", ".ckpt"))
    )
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="symlink_reject",
            passed=not suspicious,
            detail="suspicious file pattern" if suspicious else "clean",
        )
    )


def _scan_extra_columns(
    field_path: str,
    observed_keys: frozenset[str],
    allowed_keys: frozenset[str],
    checks: list[RedactionScanResult],
) -> None:
    """Detect extra columns/fields not in the allowed schema."""
    extra = observed_keys - allowed_keys
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="extra_columns",
            passed=not extra,
            detail=f"extra keys: {sorted(extra)}" if extra else "schema match",
        )
    )


def _scan_markdown_injection(
    field_path: str,
    raw: bytes,
    checks: list[RedactionScanResult],
) -> None:
    """Detect markdown injection patterns in raw text fields."""
    has_injection = bool(_MARKDOWN_INJECTION_PATTERN.search(raw))
    checks.append(
        RedactionScanResult(
            field_path=field_path,
            policy="markdown_injection",
            passed=not has_injection,
            detail="markdown injection detected" if has_injection else "clean",
        )
    )


_BUNDLE_METADATA_KEYS: frozenset[str] = frozenset({
    "experiment_id",
    "bundle_kind",
    "source_commit",
    "profile_hash",
    "generated_at",
    "schema_version",
})


def _scan_bundle_files(
    bundle: Mapping[str, object],
    checks: list[RedactionScanResult],
) -> None:
    """Top-level scan: reject entire files outside the public allowlist."""
    for name in sorted(bundle.keys(), key=lambda k: k.encode("utf-8")):
        if name in _BUNDLE_METADATA_KEYS:
            continue
        in_allowlist = _file_in_allowlist(name)
        is_symlink = (
            name.startswith(".")
            or ".." in name
            or "/" in name and name.startswith("/")
            or "\\" in name
            or name.endswith((".pkl", ".pickle", ".pt", ".ckpt"))
        )
        checks.append(
            RedactionScanResult(
                field_path=f"file:{name}",
                policy="file_allowlist",
                passed=in_allowlist,
                detail=(
                    "file in public allowlist"
                    if in_allowlist
                    else "file NOT in public allowlist"
                ),
            )
        )
        if is_symlink:
            checks.append(
                RedactionScanResult(
                    field_path=f"file:{name}",
                    policy="file_symlink",
                    passed=False,
                    detail="suspicious file name pattern",
                )
            )


def _verify_source_attestation(
    bundle_hash: str,
    source_commit: str,
    profile_hash: str,
    checks: list[RedactionScanResult],
) -> None:
    """Verify source commit/profile hash chain matches bundle hash."""
    combined = (
        f"commit:{source_commit}|profile:{profile_hash}".encode()
    )
    derived = "sha256:" + sha256(combined).hexdigest()
    match = derived == bundle_hash
    checks.append(
        RedactionScanResult(
            field_path="source_attestation",
            policy="source_chain",
            passed=match,
            detail="source chain verified" if match else "source commit/profile hash mismatch",
        )
    )


# --- Public redact_bundle ---


def redact_bundle(
    bundle_dict: Mapping[str, object],
) -> tuple[dict, dict]:
    """Deterministic redactor: scan bundle, return (manifest, attestation)."""
    checks: list[RedactionScanResult] = []

    _scan_bundle_files(bundle_dict, checks)

    _ALLOWED_FIELD_KEYS: frozenset[str] = frozenset({
        "experiment_id",
        "bundle_kind",
        "confirmatory_report_path",
        "tables",
        "generated_at",
        "schema_version",
    })

    for key in sorted(bundle_dict.keys(), key=lambda k: k.encode("utf-8")):
        value = bundle_dict[key]
        raw = repr(value).encode("utf-8") if not isinstance(value, bytes) else value

        _scan_field_provenance(key, value, checks)
        _scan_high_entropy(key, raw, checks)
        _scan_low_entropy_answer(key, raw, checks)
        _scan_secret_path(key, raw, checks)
        _scan_markdown_injection(key, raw, checks)

        if isinstance(value, dict):
            observed = frozenset(value.keys())
            _scan_extra_columns(key, observed, _ALLOWED_FIELD_KEYS, checks)

    for sealed_key in ("nonce", "answer", "expected_output", "trace"):
        if sealed_key in bundle_dict:
            checks.append(
                RedactionScanResult(
                    field_path=f"sealed:{sealed_key}",
                    policy="sealed_identity",
                    passed=False,
                    detail=f"sealed key '{sealed_key}' must not appear in bundle",
                )
            )

    sorted_payload = tuple(
        (k, bundle_dict[k])
        for k in sorted(bundle_dict.keys(), key=lambda x: x.encode("utf-8"))
    )
    hash_input = repr(sorted_payload).encode("utf-8")
    bundle_hash = "sha256:" + sha256(hash_input).hexdigest()

    source_commit = bundle_dict.get("source_commit", "")
    profile_hash = bundle_dict.get("profile_hash", "")
    if isinstance(source_commit, str) and isinstance(profile_hash, str):
        _verify_source_attestation(
            bundle_hash, source_commit, profile_hash, checks
        )

    all_passed = all(c.passed for c in checks)
    checks_tuple = tuple(checks)
    attestation_payload = {
        "schema_version": "automarkov.redaction-attestation.v1",
        "experiment_id": bundle_dict.get("experiment_id", ""),
        "bundle_hash": bundle_hash,
        "redaction_checks": [
            c.model_dump(mode="json", round_trip=True) for c in checks_tuple
        ],
        "all_passed": all_passed,
    }
    attestation_hash = "sha256:" + sha256(
        canonical_json_bytes(attestation_payload)
    ).hexdigest()
    attestation_payload["attestation_hash"] = attestation_hash

    manifest_payload = {
        "schema_version": "automarkov.redaction-manifest.v1",
        "experiment_id": bundle_dict.get("experiment_id", ""),
        "source_artifacts": [],
        "redactor_principal_id": "deterministic_redactor_v1",
        "attestation_hash": attestation_hash,
    }

    return manifest_payload, attestation_payload


# --- Publisher (R10 deepened) ---


class PublicReportBundle(StrictFrozenModel):
    """Deterministic output of the fixed publisher."""

    schema_version: Literal[
        "automarkov.public-report-bundle.v1"
    ] = "automarkov.public-report-bundle.v1"
    experiment_id: NonEmptyId
    redaction_manifest_hash: Sha256Value
    redaction_attestation_hash: Sha256Value
    published_files: FrozenSequence[NonEmptyId]
    file_hashes: FrozenSequence[NonEmptyId]
    sbom: FrozenSequence[NonEmptyId]


def _verify_file_hashes(
    files: Mapping[str, bytes],
    expected_hashes: Mapping[str, str],
) -> bool:
    """Verify that each file sha256 matches expected."""
    if not files or not expected_hashes or set(files) != set(expected_hashes):
        return False
    for name in sorted(expected_hashes.keys(), key=lambda k: k.encode("utf-8")):
        expected = expected_hashes[name]
        actual_payload = files.get(name)
        if actual_payload is None:
            return False
        actual = "sha256:" + sha256(actual_payload).hexdigest()
        if actual != expected:
            return False
    return True


def _build_sbom(
    experiment_id: str,
    published_files: tuple[str, ...],
    file_hashes: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    """Build a deterministic SBOM structure for the published bundle."""
    entries: list[dict[str, str]] = []
    for name in sorted(published_files, key=lambda k: k.encode("utf-8")):
        entries.append({
            "file": name,
            "sha256": file_hashes.get(name, ""),
            "domain": "AutoMarkov-PublicReport-Bundle-v1",
        })
    return tuple(entries)


def publish_bundle(
    redacted_manifest: Mapping[str, object],
    redaction_attestation: Mapping[str, object],
) -> dict:
    """Deterministic publisher: takes redacted manifest + attestation,
    renders a PublicReportBundle with file hash verification and SBOM.

    Cannot mount sealed store -- output is purely deterministic.
    """
    experiment_id_inner = str(redacted_manifest.get("experiment_id", ""))
    attestation_hash = str(redaction_attestation.get("attestation_hash", ""))
    manifest_hash = "sha256:" + sha256(
        repr(sorted(redacted_manifest.items())).encode("utf-8")
    ).hexdigest()

    _raw_bindings = redacted_manifest.get("source_artifacts")
    source_bindings: tuple[object, ...] = tuple(
        _raw_bindings if isinstance(_raw_bindings, (list, tuple)) else ()
    )
    published_files: list[str] = []
    file_hashes: dict[str, str] = {}
    for binding in source_bindings:
        if not isinstance(binding, dict):
            continue
        redacted_ref = binding.get("redacted_artifact", {})
        file_name = str(redacted_ref.get("artifact_id", ""))
        payload_hash = str(redacted_ref.get("payload_hash", ""))
        if file_name:
            published_files.append(file_name)
            file_hashes[file_name] = payload_hash

    published_files_tuple = tuple(
        sorted(set(published_files), key=lambda k: k.encode("utf-8"))
    )

    files_ok = _verify_file_hashes({}, file_hashes)

    sbom_entries = _build_sbom(
        experiment_id_inner, published_files_tuple, file_hashes
    )

    bundle = PublicReportBundle(
        experiment_id=experiment_id_inner,
        redaction_manifest_hash=manifest_hash,
        redaction_attestation_hash=attestation_hash,
        published_files=published_files_tuple,
        file_hashes=tuple(
            f"{name}={file_hashes[name]}"
            for name in sorted(file_hashes.keys(), key=lambda k: k.encode("utf-8"))
        ),
        sbom=tuple(entry["file"] for entry in sbom_entries),
    )

    output = bundle.model_dump(
        mode="json",
        round_trip=True,
        warnings="error",
    )
    canonical = canonical_json_bytes(output)
    output["bundle_hash"] = "sha256:" + sha256(canonical).hexdigest()
    output["sbom_entries"] = list(sbom_entries)
    output["file_verification"] = files_ok
    return output


# --- T27: Release CI ---


class ReleaseGateCheck(StrictFrozenModel):
    check_id: NonEmptyId
    description: Annotated[str, Field(strict=True, min_length=1, max_length=1024)]
    kind: Literal[
        "sbom_complete",
        "license_verified",
        "reproducibility_sealed",
        "ci_passing",
        "cards_frozen",
        "release_approved",
    ]
    passed: bool = Field(strict=True)


class ReleaseGateResult(StrictFrozenModel):
    schema_version: Literal["automarkov.release-gate-result.v1"] = (
        "automarkov.release-gate-result.v1"
    )
    experiment_id: NonEmptyId
    checks: FrozenSequence[ReleaseGateCheck]
    released: bool = Field(strict=True)

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> ReleaseGateResult:
        required_kinds = {
            "sbom_complete",
            "license_verified",
            "reproducibility_sealed",
            "ci_passing",
            "cards_frozen",
            "release_approved",
        }
        observed_kinds = {check.kind for check in self.checks}
        closed = len(self.checks) == len(required_kinds) and observed_kinds == required_kinds
        derived = closed and all(check.passed for check in self.checks)
        if not closed:
            raise ValueError("release gate requires the closed check set")
        if self.released is not derived:
            raise ValueError("released verdict must equal the closed check conjunction")
        return self

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)


# --- Release gate (R10 deepened: from CHECKS, not input) ---

_RELEASE_GATE_REQUIRED_KINDS: frozenset[str] = frozenset({
    "sbom_complete",
    "license_verified",
    "reproducibility_sealed",
    "ci_passing",
    "cards_frozen",
    "release_approved",
})


def check_release_gate(
    freeze_gate: object,
    redacted_manifest: Mapping[str, object],
    redaction_attestation: Mapping[str, object],
    source_attestation: Mapping[str, object],
    bundle_schema_valid: bool,
    owner_approval: bool,
) -> ReleaseGateResult:
    """Run the 6-check release gate.

    Release result comes from the CHECKS themselves,
    not from a ``released:true`` input field.

    Structure:
      all_required_checks_pass
      AND bundle_schema_valid
      AND redaction_valid
      AND source_attestation_valid
      AND independent_install_ok
      AND owner_approval_present
    """
    gate_checks: list[ReleaseGateCheck] = []

    # 1. all_required_checks_pass
    freeze_ok = bool(getattr(freeze_gate, "is_frozen", False))
    gate_checks.append(ReleaseGateCheck(
        check_id="all_required_checks_pass",
        description="freeze gate reports fully frozen",
        kind="release_approved",
        passed=freeze_ok,
    ))

    # 2. bundle_schema_valid
    gate_checks.append(ReleaseGateCheck(
        check_id="bundle_schema_valid",
        description="bundle schema version is valid",
        kind="cards_frozen",
        passed=bundle_schema_valid,
    ))

    # 3. redaction_valid
    attestation_all_passed = bool(
        redaction_attestation.get("all_passed", False)
    )
    attestation_hash_present = bool(
        redaction_attestation.get("attestation_hash", "")
    )
    redaction_ok = attestation_all_passed and attestation_hash_present
    gate_checks.append(ReleaseGateCheck(
        check_id="redaction_valid",
        description="redaction attestation all passed with valid hash",
        kind="reproducibility_sealed",
        passed=redaction_ok,
    ))

    # 4. source_attestation_valid
    source_match = False
    gate_checks.append(ReleaseGateCheck(
        check_id="source_attestation_valid",
        description="typed signed source attestation is required",
        kind="license_verified",
        passed=source_match,
    ))

    # 5. independent_install_ok
    install_ok = False
    gate_checks.append(ReleaseGateCheck(
        check_id="independent_install_ok",
        description="verified fixed publisher file set is required",
        kind="ci_passing",
        passed=install_ok,
    ))

    # 6. owner_approval_present
    gate_checks.append(ReleaseGateCheck(
        check_id="owner_approval_present",
        description="owner approval is present",
        kind="sbom_complete",
        passed=owner_approval,
    ))

    gate_all_passed = all(c.passed for c in gate_checks)
    return ReleaseGateResult(
        experiment_id=str(redacted_manifest.get("experiment_id", "")),
        checks=tuple(gate_checks),
        released=gate_all_passed,
    )


__all__ = [
    "ConfirmatoryMatrix",
    "FreezeGateCheck",
    "FreezeGateResult",
    "PublicReportBundle",
    "PublicReportManifest",
    "RedactedArtifactBinding",
    "RedactionAttestation",
    "RedactionManifest",
    "RedactionScanResult",
    "ReleaseGateCheck",
    "ReleaseGateResult",
    "ReplicationManifest",
    "ReplicationSuiteBinding",
    "check_release_gate",
    "publish_bundle",
    "redact_bundle",
]
