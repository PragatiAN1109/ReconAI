"""The final-result contract, asserted from both sides at once.

WHY THIS FILE EXISTS
--------------------
Investigation INV-1004 failed in the deployed environment with:

    Investigation result failed schema validation
    [investigation_id=INV-1004 problems=[{'field': 'confidence',
                                          'problem': 'Field required'}]]

Two contracts describe one thing — the JSON Schema handed to the provider and
``InvestigationResult`` — and nothing checked that they agreed. These tests do.

A NOTE ON WHAT A SCHEMA CAN AND CANNOT DO
-----------------------------------------
``confidence`` was **already** in the schema's ``required`` list when INV-1004
omitted it. A tool ``input_schema``'s ``required`` is instruction to the model,
not an API-side validator. So the agreement tests below prevent a *drift* bug —
a constraint Pydantic enforces that the model was never told about — and the
rejection tests prove that when the model disregards the schema anyway, the
result is refused rather than stored. Both halves matter; neither alone is
enough.

Nothing here relaxes ``InvestigationResult``. Every rejection test asserts a
rejection.
"""

import json

import pytest
from pydantic import ValidationError

from app.anthropic_model import _SUBMIT_RESULT_SPECIFICATION, SUBMIT_RESULT_TOOL
from app.investigation_models import (
    NARRATIVE_MAX_LENGTH,
    EvidenceSource,
    InvestigationResult,
    RootCauseClassification,
    describe_payload,
    result_input_schema,
)

SCHEMA = result_input_schema()
PROPERTIES = SCHEMA["properties"]
EVIDENCE_ITEM = PROPERTIES["evidence"]["items"]


def valid_payload(**overrides) -> dict:
    """A result that satisfies both contracts, before any override."""
    payload = {
        "classification": "PROCESSOR_FEE",
        "rootCause": "A processing fee matches the settlement difference.",
        "confidence": 0.86,
        "evidence": [{"sourceType": "FEE_RULE", "reference": "FR-14"}],
        "recommendedAction": "Classify as a processor fee adjustment.",
        "requiresHumanApproval": True,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# The two contracts agree
# ---------------------------------------------------------------------------


class TestRequiredFieldsAgree:
    def test_the_baseline_payload_satisfies_pydantic(self) -> None:
        # If this fails, every other test in the file is meaningless.
        assert InvestigationResult.model_validate(valid_payload()).confidence == 0.86

    def test_every_field_pydantic_requires_is_required_by_the_schema(self) -> None:
        """The INV-1004 class of bug, asserted generically.

        Derived from the model rather than listed, so a newly added required
        field is caught without anyone remembering to update this test.
        """
        required_by_model = {
            field.alias or name
            for name, field in InvestigationResult.model_fields.items()
            if field.is_required()
        }

        assert required_by_model <= set(SCHEMA["required"])

    def test_confidence_specifically_is_required_by_both(self) -> None:
        # Named explicitly because this is the field that actually failed.
        assert InvestigationResult.model_fields["confidence"].is_required()
        assert "confidence" in SCHEMA["required"]

    def test_the_schema_describes_every_field_the_model_accepts(self) -> None:
        aliases = {
            field.alias or name for name, field in InvestigationResult.model_fields.items()
        }

        assert set(PROPERTIES) == aliases

    def test_the_schema_invents_no_field_the_model_would_reject(self) -> None:
        # extra="forbid" means any property here that the model does not know
        # would be solicited and then rejected — exactly the asymmetry to avoid.
        for name in PROPERTIES:
            assert InvestigationResult.model_validate(valid_payload()) is not None
            assert name in {
                field.alias or field_name
                for field_name, field in InvestigationResult.model_fields.items()
            }


class TestEnumDomainsAgree:
    def test_classification_enum_matches_the_python_enum(self) -> None:
        assert PROPERTIES["classification"]["enum"] == [
            member.value for member in RootCauseClassification
        ]

    def test_evidence_source_enum_matches_the_python_enum(self) -> None:
        assert EVIDENCE_ITEM["properties"]["sourceType"]["enum"] == [
            member.value for member in EvidenceSource
        ]

    def test_processor_fee_is_offered_as_a_root_cause(self) -> None:
        # It is an AI classification and must stay available here — and it must
        # never appear in the deterministic ExceptionType, which is asserted in
        # the financial core's own tests.
        assert "PROCESSOR_FEE" in PROPERTIES["classification"]["enum"]

    def test_insufficient_evidence_is_offered(self) -> None:
        # A model that cannot support a conclusion needs a way to say so, or it
        # will guess.
        assert "INSUFFICIENT_EVIDENCE" in PROPERTIES["classification"]["enum"]


class TestConstraintsAgree:
    def test_confidence_bounds_agree(self) -> None:
        # Read explicitly rather than with `or`: the lower bound is 0.0, which is
        # falsy, so a truthiness-based lookup silently reports it as absent.
        metadata = InvestigationResult.model_fields["confidence"].metadata
        lower = next(item.ge for item in metadata if hasattr(item, "ge"))
        upper = next(item.le for item in metadata if hasattr(item, "le"))

        assert (lower, upper) == (0.0, 1.0)
        assert PROPERTIES["confidence"]["minimum"] == lower
        assert PROPERTIES["confidence"]["maximum"] == upper

    @pytest.mark.parametrize("confidence", [-0.01, 1.01])
    def test_confidence_outside_the_bounds_is_rejected(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(valid_payload(confidence=confidence))

    @pytest.mark.parametrize("confidence", [0.0, 1.0])
    def test_the_bounds_themselves_are_accepted(self, confidence: float) -> None:
        # Zero confidence is a legitimate answer and must not be rejected.
        assert InvestigationResult.model_validate(valid_payload(confidence=confidence))

    @pytest.mark.parametrize("field_alias", ["rootCause", "recommendedAction"])
    def test_narrative_length_constraints_agree(self, field_alias: str) -> None:
        assert PROPERTIES[field_alias]["maxLength"] == NARRATIVE_MAX_LENGTH
        assert PROPERTIES[field_alias]["minLength"] == 1

    def test_evidence_reference_requires_a_non_empty_string_in_both(self) -> None:
        assert EVIDENCE_ITEM["properties"]["reference"]["minLength"] == 1
        assert "reference" in EVIDENCE_ITEM["required"]

    def test_evidence_source_type_is_required_in_both(self) -> None:
        assert "sourceType" in EVIDENCE_ITEM["required"]

    def test_section_is_optional_and_nullable_in_both(self) -> None:
        """The one axis where the schema must NOT be stricter than Pydantic.

        ``section: str | None = None`` accepts an explicit null. A schema saying
        ``{"type": "string"}`` would solicit a result the model could not
        legally express as absent-but-present.
        """
        assert "section" not in EVIDENCE_ITEM["required"]
        assert set(EVIDENCE_ITEM["properties"]["section"]["type"]) == {"string", "null"}
        assert InvestigationResult.model_validate(
            valid_payload(
                evidence=[{"sourceType": "POLICY_DOCUMENT", "reference": "POL-1", "section": None}]
            )
        )

    def test_extra_properties_are_forbidden_consistently(self) -> None:
        assert SCHEMA["additionalProperties"] is False
        assert EVIDENCE_ITEM["additionalProperties"] is False
        assert InvestigationResult.model_config["extra"] == "forbid"

    def test_requires_human_approval_admits_only_true(self) -> None:
        assert PROPERTIES["requiresHumanApproval"]["enum"] == [True]


class TestTheToolSpecification:
    def test_the_adapter_ships_the_generated_schema(self) -> None:
        # Not a copy that could drift from the generator.
        assert _SUBMIT_RESULT_SPECIFICATION["input_schema"] == result_input_schema()

    def test_the_tool_is_named_and_described(self) -> None:
        assert _SUBMIT_RESULT_SPECIFICATION["name"] == SUBMIT_RESULT_TOOL
        assert "confidence" in _SUBMIT_RESULT_SPECIFICATION["description"]

    def test_the_specification_is_json_serialisable(self) -> None:
        # It crosses the wire to the provider; a non-serialisable schema would
        # fail every call rather than one result.
        assert json.loads(json.dumps(_SUBMIT_RESULT_SPECIFICATION))


# ---------------------------------------------------------------------------
# Pydantic still rejects, whatever the model sends
# ---------------------------------------------------------------------------


class TestPydanticStillRejects:
    def test_a_result_without_confidence_is_rejected(self) -> None:
        """The exact INV-1004 regression."""
        payload = valid_payload()
        del payload["confidence"]

        with pytest.raises(ValidationError) as failure:
            InvestigationResult.model_validate(payload)

        problems = {error["loc"][0]: error["type"] for error in failure.value.errors()}
        assert problems == {"confidence": "missing"}

    def test_a_root_cause_over_the_limit_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(
                valid_payload(rootCause="x" * (NARRATIVE_MAX_LENGTH + 1))
            )

    def test_a_root_cause_at_the_limit_is_accepted(self) -> None:
        # The boundary is inclusive; rejecting exactly 2000 would be a different bug.
        assert InvestigationResult.model_validate(
            valid_payload(rootCause="x" * NARRATIVE_MAX_LENGTH)
        )

    def test_a_recommended_action_over_the_limit_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(
                valid_payload(recommendedAction="x" * (NARRATIVE_MAX_LENGTH + 1))
            )

    def test_an_empty_recommended_action_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(valid_payload(recommendedAction=""))

    def test_an_empty_evidence_reference_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(
                valid_payload(evidence=[{"sourceType": "FEE_RULE", "reference": ""}])
            )

    def test_an_extra_top_level_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(valid_payload(shouldEscalate=True))

    def test_an_extra_evidence_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(
                valid_payload(
                    evidence=[
                        {"sourceType": "FEE_RULE", "reference": "FR-14", "certainty": "high"}
                    ]
                )
            )

    def test_requires_human_approval_false_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InvestigationResult.model_validate(valid_payload(requiresHumanApproval=False))

    def test_snake_case_field_names_are_also_accepted(self) -> None:
        # populate_by_name=True. Asserted so nobody "fixes" the aliases by
        # removing it and breaks a payload shape that currently works.
        assert InvestigationResult.model_validate(
            {
                "classification": "UNKNOWN",
                "root_cause": "unclear",
                "confidence": 0.1,
                "evidence": [],
                "recommended_action": "escalate",
                "requires_human_approval": True,
            }
        )


# ---------------------------------------------------------------------------
# The diagnostic is safe to log
# ---------------------------------------------------------------------------


class TestDescribePayload:
    def test_it_reports_a_missing_field_as_null(self) -> None:
        payload = valid_payload()
        del payload["confidence"]

        assert describe_payload(payload) == '{"classification": "PROCESSOR_FEE", "confidence": null}'

    def test_it_carries_no_narrative_or_evidence_detail(self) -> None:
        described = describe_payload(
            valid_payload(rootCause="SENSITIVE NARRATIVE", recommendedAction="SENSITIVE ACTION")
        )

        assert "SENSITIVE" not in described
        assert "FR-14" not in described

    def test_it_handles_an_absent_payload(self) -> None:
        assert describe_payload(None) == "null"
