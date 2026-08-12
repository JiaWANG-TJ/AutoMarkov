from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from automarkov.repository import InMemoryArtifactRepository, SqliteArtifactRepository
from automarkov.validation_contracts import (
    ValidationClaim,
    validate_claim_report_chain,
    validate_validation_claim_payload,
    validate_validation_report_payload,
)


def _ref(digit: str) -> dict[str, str]:
    return {
        "artifact_id": f"artifact_{digit * 64}",
        "payload_hash": f"sha256:{digit * 64}",
    }


def _report(level: str, *, digit: str, status: str = "passed") -> dict[str, object]:
    return {
        "schema_version": "automarkov.validation-report.v1",
        "report_kind": "validation_report",
        "subject_ref": _ref("1"),
        "level": level,
        "validator_id": f"validator_{level}",
        "validator_version": "1.0.0",
        "status": status,
        "scope": ["transition_kernel"],
        "covered_scope": ["transition_kernel"],
        "uncovered_scope": [],
        "assumptions": [],
        "proof_refs": [_ref(digit)] if level == "formally_verified" else [],
        "formal_evidence": (
            {
                "property_id": "transition_totality",
                "model_id": "inventory_model_v1",
                "tool_id": "proof_checker_v1",
            }
            if level == "formally_verified"
            else None
        ),
    }


def test_formal_report_requires_property_model_tool_and_proof() -> None:
    report = validate_validation_report_payload(_report("formally_verified", digit="9"))
    assert report.formal_evidence is not None

    payload = _report("formally_verified", digit="9")
    payload["proof_refs"] = []
    with pytest.raises((ValueError, ValidationError)):
        validate_validation_report_payload(payload)


def test_high_level_claim_requires_all_lower_passed_reports_for_same_scope() -> None:
    levels = (
        "schema",
        "structural",
        "executable",
        "behavioral",
        "oracle_equivalent",
    )
    reports = tuple(
        validate_validation_report_payload(_report(level, digit=str(index + 2)))
        for index, level in enumerate(levels)
    )
    report_refs = tuple(
        {
            "artifact_id": f"artifact_{(index + 2):x}".ljust(73, f"{(index + 2):x}"),
            "payload_hash": f"sha256:{(index + 2):x}".ljust(71, f"{(index + 2):x}"),
        }
        for index in range(len(reports))
    )
    claim_payload = {
        "schema_version": "automarkov.validation-claim.v1",
        "claim_kind": "validation_claim",
        "subject_ref": _ref("1"),
        "report_refs": list(report_refs),
        "level": "oracle_equivalent",
        "scope": ["transition_kernel"],
        "passed": True,
    }
    claim = validate_validation_claim_payload(claim_payload)
    assert isinstance(claim, ValidationClaim)
    validate_claim_report_chain(
        claim, tuple(zip(claim.report_refs, reports, strict=True))
    )

    incomplete_payload = dict(claim_payload)
    incomplete_payload["report_refs"] = list(report_refs[1:])
    incomplete_claim = validate_validation_claim_payload(incomplete_payload)
    with pytest.raises(ValueError, match="prerequisite"):
        validate_claim_report_chain(
            incomplete_claim,
            tuple(zip(incomplete_claim.report_refs, reports[1:], strict=True)),
        )


def test_failed_report_cannot_support_a_claim() -> None:
    report = validate_validation_report_payload(
        _report("schema", digit="2", status="failed")
    )
    claim = validate_validation_claim_payload(
        {
            "schema_version": "automarkov.validation-claim.v1",
            "claim_kind": "validation_claim",
            "subject_ref": _ref("1"),
            "report_refs": [_ref("3")],
            "level": "schema",
            "scope": ["transition_kernel"],
            "passed": True,
        }
    )
    with pytest.raises(ValueError, match="passed"):
        validate_claim_report_chain(claim, ((claim.report_refs[0], report),))


@pytest.mark.parametrize("repository_kind", ("memory", "sqlite"))
def test_default_repository_persists_the_exact_six_level_claim_dag(
    repository_kind: str,
    tmp_path,
) -> None:
    repository = (
        InMemoryArtifactRepository()
        if repository_kind == "memory"
        else SqliteArtifactRepository(tmp_path / "validation-claims.sqlite3")
    )

    def put(
        artifact_type: str,
        payload: dict[str, object],
        parent_ids: tuple[str, ...] = (),
    ):
        return repository.put(
            {
                "schema_version": "automarkov.artifact-put-request.v2",
                "artifact_type": artifact_type,
                "payload_bytes": json.dumps(payload).encode(),
                "parent_artifact_ids": sorted(parent_ids),
                "created_by": "principal_validator",
                "created_at": "2026-08-12T00:00:00Z",
                "source_evidence_ids": [],
            }
        )

    def task(request_id: str):
        return put(
            "task_request",
            {
                "schema_version": "automarkov.task-request.v1",
                "request_id": request_id,
                "task_text": "Validate an immutable subject.",
                "budget": {
                    "schema_version": "automarkov.request-budget.v1",
                    "wall_time_seconds": 30,
                    "llm_token_limit": 0,
                    "tool_call_limit": 0,
                },
                "permissions": {
                    "schema_version": "automarkov.request-permissions.v1",
                    "allow_retrieval": False,
                    "allow_clarification": False,
                    "allow_code_execution": False,
                },
            },
        )

    subject = task("request_validation_subject")
    proof = task("request_validation_proof")
    subject_ref = {
        "artifact_id": subject.artifact_id.root,
        "payload_hash": subject.payload_hash.root,
    }
    report_results = []
    for index, level in enumerate(
        (
            "schema",
            "structural",
            "executable",
            "behavioral",
            "oracle_equivalent",
            "formally_verified",
        ),
        start=1,
    ):
        formal = level == "formally_verified"
        report_results.append(
            put(
                "validation_report",
                {
                    **_report(level, digit=str(index + 1)),
                    "subject_ref": subject_ref,
                    "proof_refs": (
                        [
                            {
                                "artifact_id": proof.artifact_id.root,
                                "payload_hash": proof.payload_hash.root,
                            }
                        ]
                        if formal
                        else []
                    ),
                },
                (
                    subject.artifact_id.root,
                    *((proof.artifact_id.root,) if formal else ()),
                ),
            )
        )
    report_refs = [
        {
            "artifact_id": report.artifact_id.root,
            "payload_hash": report.payload_hash.root,
        }
        for report in report_results
    ]
    claim = put(
        "validation_claim",
        {
            "schema_version": "automarkov.validation-claim.v1",
            "claim_kind": "validation_claim",
            "subject_ref": subject_ref,
            "report_refs": report_refs,
            "level": "formally_verified",
            "scope": ["transition_kernel"],
            "passed": True,
        },
        (subject.artifact_id.root, *(item.artifact_id.root for item in report_results)),
    )

    assert repository.get(claim.artifact_id).envelope.artifact_type == (
        "validation_claim"
    )
    if isinstance(repository, SqliteArtifactRepository):
        repository.close()
