"""The one structured-result correction, and its limits.

WHY THIS EXISTS
---------------
INV-1004 completed its investigation — six tool calls across transaction,
settlements, fee rules and policy — and then submitted its conclusion without
``confidence``. ``confidence`` was already in the tool schema's ``required``
array, which is how we learned that a tool ``input_schema`` is guidance to the
model rather than an API-side validator.

So the model gets exactly one chance to submit a corrected result. What these
tests protect is that the chance is **one**, that the correction is validated by
the same Pydantic model as the original, that it is still checked against the
evidence ledger, and that the application never fills in a missing field itself.

The first test is the INV-1004 regression.
"""

import json

import pytest

from app.events import ExceptionType
from app.investigation_agent import (
    InvestigationAgent,
    InvestigationFailed,
    UngroundedResultError,
)
from app.investigation_model import (
    SUBMIT_RESULT_TOOL,
    AssistantTurn,
    InvestigationModelError,
    ToolResults,
)
from app.investigation_models import InvestigationContext, RootCauseClassification
from tests.fake_model import FakeModel, final_turn, tool_turn
from tests.test_investigation_agent import POLICY_CORPUS, routing_core  # noqa: F401

CONTEXT = InvestigationContext(
    investigationId="INV-1004",
    exceptionId="EX-2001",
    transactionId="TX-10009",
    exceptionType=ExceptionType.AMOUNT_MISMATCH,
)


@pytest.fixture
def policies(tmp_path):
    for name, content in POLICY_CORPUS.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    from app.policy_search import PolicySearch  # noqa: PLC0415

    return PolicySearch(tmp_path)


def submitted(payload_overrides: dict, call_id: str = "toolu_submit_1") -> AssistantTurn:
    """A final turn carrying a provider-assigned identifier.

    The identifier is what makes a correction addressable: a provider matches a
    tool result to the tool call it answers, so without one there is nothing to
    reply to.
    """
    turn = final_turn(**payload_overrides)
    return AssistantTurn(final_result=turn.final_result, final_call_id=call_id)


def agent(model: FakeModel, policies, **kwargs) -> InvestigationAgent:
    return InvestigationAgent(model, routing_core(), policies, **kwargs)


def grounded_evidence() -> list[dict]:
    return [
        {"sourceType": "TRANSACTION", "reference": "TX-10009"},
        {"sourceType": "SETTLEMENT", "reference": "SET-8008"},
    ]


def gathering_turns() -> list[AssistantTurn]:
    """Two tool rounds, so the ledger holds real evidence to cite."""
    return [
        tool_turn("get_transaction", {"transaction_id": "TX-10009"}),
        tool_turn("get_settlements", {"transaction_id": "TX-10009"}),
    ]


# ---------------------------------------------------------------------------
# 1. The INV-1004 regression
# ---------------------------------------------------------------------------


async def test_a_missing_confidence_is_corrected_and_the_investigation_succeeds(
    policies,
) -> None:
    """INV-1004, replayed: submit without confidence, then correct it.

    The live failure. The first submission omits ``confidence``; the model is
    told which field was wrong and submits a complete replacement; the
    investigation completes normally.
    """
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ..., "evidence": grounded_evidence()}),
            submitted({"confidence": 0.91, "evidence": grounded_evidence()}, "toolu_2"),
        ]
    )

    result, ledger = await agent(model, policies).investigate(CONTEXT)

    assert result.confidence == 0.91
    assert result.classification is RootCauseClassification.PROCESSOR_FEE
    assert ledger.transactions == {"TX-10009"}


async def test_the_correction_request_names_the_offending_field_and_nothing_else(
    policies,
) -> None:
    """Safe structural feedback only — no payload, no narrative, no evidence."""
    model = FakeModel(
        [
            *gathering_turns(),
            submitted(
                {
                    "confidence": ...,
                    "rootCause": "SENSITIVE NARRATIVE ABOUT THE MERCHANT",
                    "evidence": grounded_evidence(),
                }
            ),
            submitted({"confidence": 0.9, "evidence": grounded_evidence()}, "toolu_2"),
        ]
    )

    await agent(model, policies).investigate(CONTEXT)

    transcript = model.seen_transcripts[-1]
    feedback = next(
        entry for entry in transcript if isinstance(entry, ToolResults)
        and entry.results[0].tool == SUBMIT_RESULT_TOOL
    )
    payload = json.loads(feedback.results[0].content)

    assert payload["accepted"] is False
    assert payload["validationErrors"] == [{"field": "confidence", "problem": "Field required"}]
    assert "only correction" in payload["instruction"]
    # Nothing from the rejected submission travels back.
    assert "SENSITIVE" not in feedback.results[0].content
    assert "TX-10009" not in feedback.results[0].content
    # Addressed to the rejected submission, and marked as a rejection.
    assert feedback.results[0].call_id == "toolu_submit_1"
    assert feedback.results[0].failed is True


# ---------------------------------------------------------------------------
# 2, 3. At most one correction
# ---------------------------------------------------------------------------


async def test_a_second_malformed_submission_fails_the_investigation(policies) -> None:
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": ...}, "toolu_2"),
        ]
    )

    with pytest.raises(InvestigationFailed, match="after its single correction"):
        await agent(model, policies).investigate(CONTEXT)


async def test_the_correction_is_offered_at_most_once(policies) -> None:
    """Three malformed submissions must not produce three corrections."""
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": ...}, "toolu_2"),
            submitted({"confidence": ...}, "toolu_3"),
        ]
    )

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)

    # Two gathering turns plus two submissions. The third was never requested.
    assert len(model.seen_transcripts) == 4


async def test_a_submission_with_no_identifier_cannot_be_corrected(policies) -> None:
    """Without an identifier there is nothing to address a correction to."""
    model = FakeModel([*gathering_turns(), final_turn(confidence=...)])

    with pytest.raises(InvestigationFailed, match="malformed result"):
        await agent(model, policies).investigate(CONTEXT)


# ---------------------------------------------------------------------------
# 4, 5, 6, 7. A corrected result is validated exactly like the original
# ---------------------------------------------------------------------------


async def test_a_corrected_result_still_undergoes_evidence_grounding(policies) -> None:
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": 0.9, "evidence": grounded_evidence()}, "toolu_2"),
        ]
    )

    result, ledger = await agent(model, policies).investigate(CONTEXT)

    assert [reference.reference for reference in result.evidence] == ["TX-10009", "SET-8008"]
    assert ledger.ungrounded(result.evidence) == []


async def test_a_corrected_result_citing_fabricated_evidence_fails(policies) -> None:
    """Correction is not an amnesty. The ledger still decides."""
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted(
                {
                    "confidence": 0.9,
                    "evidence": [{"sourceType": "FEE_RULE", "reference": "FR-999"}],
                },
                "toolu_2",
            ),
        ]
    )

    with pytest.raises(UngroundedResultError, match="FR-999"):
        await agent(model, policies).investigate(CONTEXT)


async def test_a_corrected_result_waiving_human_approval_fails(policies) -> None:
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": 0.9, "requiresHumanApproval": False}, "toolu_2"),
        ]
    )

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)


async def test_a_corrected_result_with_an_extra_field_fails(policies) -> None:
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": 0.9, "shouldEscalate": True}, "toolu_2"),
        ]
    )

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)


@pytest.mark.parametrize(
    ("name", "override"),
    [
        ("confidence out of range", {"confidence": 1.5}),
        ("unknown classification", {"classification": "PROCESSOR_GREED"}),
        ("root cause too long", {"rootCause": "x" * 2001}),
        ("empty recommended action", {"recommendedAction": ""}),
    ],
)
async def test_every_original_constraint_still_applies_after_correction(
    policies, name: str, override: dict
) -> None:
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": 0.9, **override}, "toolu_2"),
        ]
    )

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)


# ---------------------------------------------------------------------------
# 8. The application never fills anything in
# ---------------------------------------------------------------------------


async def test_no_value_is_supplied_by_the_application(policies) -> None:
    """The model submits a complete replacement, or the investigation fails.

    If the application ever patched a missing field, a malformed submission
    followed by an *identical* malformed submission would succeed. It must not.
    """
    malformed = {"confidence": ..., "evidence": grounded_evidence()}
    model = FakeModel(
        [
            *gathering_turns(),
            submitted(dict(malformed)),
            submitted(dict(malformed), "toolu_2"),
        ]
    )

    with pytest.raises(InvestigationFailed):
        await agent(model, policies).investigate(CONTEXT)


async def test_the_corrected_confidence_is_the_models_own_value(policies) -> None:
    # Not defaulted, not clamped, not averaged — whatever the model said.
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": 0.03, "evidence": grounded_evidence()}, "toolu_2"),
        ]
    )

    result, _ = await agent(model, policies).investigate(CONTEXT)

    assert result.confidence == 0.03


# ---------------------------------------------------------------------------
# 9. A valid submission is never corrected
# ---------------------------------------------------------------------------


async def test_a_valid_first_submission_asks_for_no_correction(policies) -> None:
    model = FakeModel([*gathering_turns(), submitted({"evidence": grounded_evidence()})])

    result, _ = await agent(model, policies).investigate(CONTEXT)

    assert result.confidence == 0.9
    transcript = model.seen_transcripts[-1]
    assert not any(
        isinstance(entry, ToolResults) and entry.results[0].tool == SUBMIT_RESULT_TOOL
        for entry in transcript
    )


# ---------------------------------------------------------------------------
# 10, 11. The correction is a provider call and is metered like one
# ---------------------------------------------------------------------------


async def test_the_correction_consumes_a_unit_of_the_shared_budget(policies) -> None:
    spent: list[bool] = []

    def permit() -> bool:
        spent.append(True)
        return True

    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            submitted({"confidence": 0.9, "evidence": grounded_evidence()}, "toolu_2"),
        ]
    )

    await agent(model, policies, repair_permit=permit).investigate(CONTEXT)

    assert spent == [True], "the correction asked for exactly one unit of budget"


async def test_a_valid_submission_asks_for_no_budget(policies) -> None:
    spent: list[bool] = []
    model = FakeModel([*gathering_turns(), submitted({"evidence": grounded_evidence()})])

    await agent(
        model, policies, repair_permit=lambda: spent.append(True) or True
    ).investigate(CONTEXT)

    assert spent == []


async def test_an_exhausted_budget_makes_no_correction_call_and_fails(policies) -> None:
    """The documented state is FAILED, not PENDING.

    The run happened: tools were called, money was spent, and the model produced
    a conclusion — an unusable one. Recording that as "not yet run" would be
    false about all of it. The message says the correction was not attempted, so
    the trail distinguishes this from a correction that also failed.
    """
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
            # Present but must never be reached.
            submitted({"confidence": 0.9, "evidence": grounded_evidence()}, "toolu_2"),
        ]
    )

    with pytest.raises(InvestigationFailed, match="AI budget for this window is exhausted"):
        await agent(model, policies, repair_permit=lambda: False).investigate(CONTEXT)

    # Two gathering turns plus the one rejected submission. No correction call.
    assert len(model.seen_transcripts) == 3


# ---------------------------------------------------------------------------
# 12. A provider failure during correction stays a provider failure
# ---------------------------------------------------------------------------


async def test_a_provider_error_during_correction_is_not_mistaken_for_malformed_output(
    policies,
) -> None:
    """The two have different consequences and must not be conflated.

    A malformed result is terminal; a provider outage is retryable and releases
    the investigation back to PENDING. If the correction call fails at the
    provider, that is the second kind.
    """
    model = FakeModel(
        [
            *gathering_turns(),
            submitted({"confidence": ...}),
        ],
        raises_after=InvestigationModelError("provider unreachable"),
    )

    with pytest.raises(InvestigationModelError):
        await agent(model, policies).investigate(CONTEXT)
