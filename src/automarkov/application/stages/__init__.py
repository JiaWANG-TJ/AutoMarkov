"""Stage registry for the 16-stage compile pipeline.

Each stage module exports its input/output model pair and its
stage function.  The ``STAGE_REGISTRY`` dict maps ``StageId`` to
``(input_model, output_model, stage_function)``, and
``STAGE_FUNCTIONS`` is a convenience dict mapping ``StageId``
directly to the callable.
"""

from __future__ import annotations

from typing import Any

from automarkov.application.stages.stage1_validate_ingress import (
    ValidateIngressInput,
    ValidateIngressOutput,
    validate_ingress_stage,
)
from automarkov.application.stages.stage2_create_manifest import (
    CreateManifestInput,
    CreateManifestOutput,
    create_manifest_stage,
)
from automarkov.application.stages.stage3_plan_evidence import (
    PlanEvidenceInput,
    PlanEvidenceOutput,
    plan_evidence_stage,
)
from automarkov.application.stages.stage4_retrieve_evidence import (
    RetrieveEvidenceInput,
    RetrieveEvidenceOutput,
    retrieve_evidence_stage,
)
from automarkov.application.stages.stage5_claim_evidence_graph import (
    ClaimEvidenceGraphInput,
    ClaimEvidenceGraphOutput,
    claim_evidence_graph_stage,
)
from automarkov.application.stages.stage6_identify_ambiguities import (
    IdentifyAmbiguitiesInput,
    IdentifyAmbiguitiesOutput,
    identify_ambiguities_stage,
)
from automarkov.application.stages.stage7_classify_process_kind import (
    ClassifyProcessKindInput,
    ClassifyProcessKindOutput,
    classify_process_kind_stage,
)
from automarkov.application.stages.stage8_propose_formal_spec import (
    ProposeFormalSpecInput,
    ProposeFormalSpecOutput,
    propose_formal_spec_stage,
)
from automarkov.application.stages.stage9_formal_validation import (
    FormalValidationInput,
    FormalValidationOutput,
    formal_validation_stage,
)
from automarkov.application.stages.stage10_text_formal_critic import (
    TextFormalCriticInput,
    TextFormalCriticOutput,
    text_formal_critic_stage,
)
from automarkov.application.stages.stage11_approval_gate import (
    ApprovalGateInput,
    ApprovalGateOutput,
    approval_gate_stage,
)
from automarkov.application.stages.stage12_select_route import (
    SelectRouteInput,
    SelectRouteOutput,
    select_route_stage,
)
from automarkov.application.stages.stage13_environment_candidate import (
    EnvironmentCandidateInput,
    EnvironmentCandidateOutput,
    environment_candidate_stage,
)
from automarkov.application.stages.stage14_public_tests import (
    PublicTestsInput,
    PublicTestsOutput,
)
from automarkov.application.stages.stage15_package_candidate import (
    PackageCandidateInput,
    PackageCandidateOutput,
)
from automarkov.application.stages.stage16_terminal_cas import (
    TerminalCASInput,
    TerminalCASOutput,
)
from automarkov.domain.errors import CapabilityDeferredError


def _deferred_public_tests(
    stage_input: object,
    *,
    recovery_head: object | None = None,
) -> Any:
    del stage_input, recovery_head
    raise CapabilityDeferredError("compiler.public_tests", "R06")


def _deferred_package_candidate(
    stage_input: object,
    *,
    recovery_head: object | None = None,
) -> Any:
    del stage_input, recovery_head
    raise CapabilityDeferredError("compiler.package_candidate", "R10")


def _deferred_terminal_cas(
    stage_input: object,
    *,
    recovery_head: object | None = None,
) -> Any:
    del stage_input, recovery_head
    raise CapabilityDeferredError("compiler.terminal_cas", "R10")

# ---------------------------------------------------------------------------
# Registry: stage_id → (input_model, output_model, stage_function)
# ---------------------------------------------------------------------------

STAGE_REGISTRY: dict[str, tuple[type[Any], type[Any], Any]] = {
    "validate_ingress": (
        ValidateIngressInput, ValidateIngressOutput, validate_ingress_stage,
    ),
    "create_manifest": (
        CreateManifestInput, CreateManifestOutput, create_manifest_stage,
    ),
    "plan_evidence": (
        PlanEvidenceInput, PlanEvidenceOutput, plan_evidence_stage,
    ),
    "retrieve_evidence": (
        RetrieveEvidenceInput, RetrieveEvidenceOutput, retrieve_evidence_stage,
    ),
    "claim_evidence_graph": (
        ClaimEvidenceGraphInput, ClaimEvidenceGraphOutput, claim_evidence_graph_stage,
    ),
    "identify_ambiguities": (
        IdentifyAmbiguitiesInput, IdentifyAmbiguitiesOutput,
        identify_ambiguities_stage,
    ),
    "classify_process_kind": (
        ClassifyProcessKindInput, ClassifyProcessKindOutput,
        classify_process_kind_stage,
    ),
    "propose_formal_spec": (
        ProposeFormalSpecInput, ProposeFormalSpecOutput,
        propose_formal_spec_stage,
    ),
    "formal_validation": (
        FormalValidationInput, FormalValidationOutput, formal_validation_stage,
    ),
    "text_formal_critic": (
        TextFormalCriticInput, TextFormalCriticOutput, text_formal_critic_stage,
    ),
    "approval_gate": (
        ApprovalGateInput, ApprovalGateOutput, approval_gate_stage,
    ),
    "select_route": (
        SelectRouteInput, SelectRouteOutput, select_route_stage,
    ),
    "environment_candidate": (
        EnvironmentCandidateInput, EnvironmentCandidateOutput,
        environment_candidate_stage,
    ),
    "public_tests": (
        PublicTestsInput, PublicTestsOutput, _deferred_public_tests,
    ),
    "package_candidate": (
        PackageCandidateInput, PackageCandidateOutput, _deferred_package_candidate,
    ),
    "terminal_cas": (
        TerminalCASInput, TerminalCASOutput, _deferred_terminal_cas,
    ),
}

STAGE_FUNCTIONS: dict[str, Any] = {
    sid: fn for sid, (_in, _out, fn) in STAGE_REGISTRY.items()
}

__all__ = [
    "STAGE_FUNCTIONS",
    "STAGE_REGISTRY",
    "ApprovalGateInput",
    "ApprovalGateOutput",
    "ClaimEvidenceGraphInput",
    "ClaimEvidenceGraphOutput",
    "ClassifyProcessKindInput",
    "ClassifyProcessKindOutput",
    "CreateManifestInput",
    "CreateManifestOutput",
    "EnvironmentCandidateInput",
    "EnvironmentCandidateOutput",
    "FormalValidationInput",
    "FormalValidationOutput",
    "IdentifyAmbiguitiesInput",
    "IdentifyAmbiguitiesOutput",
    "PackageCandidateInput",
    "PackageCandidateOutput",
    "PlanEvidenceInput",
    "PlanEvidenceOutput",
    "ProposeFormalSpecInput",
    "ProposeFormalSpecOutput",
    "PublicTestsInput",
    "PublicTestsOutput",
    "RetrieveEvidenceInput",
    "RetrieveEvidenceOutput",
    "SelectRouteInput",
    "SelectRouteOutput",
    "TerminalCASInput",
    "TerminalCASOutput",
    "TextFormalCriticInput",
    "TextFormalCriticOutput",
    "ValidateIngressInput",
    "ValidateIngressOutput",
]
