"""Tests for the Kafka event contract.

The payloads here are the exact shape the Spring producer emits.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.events import ExceptionType, ReconciliationExceptionEvent

# Copied from an event observed on the real topic.
SPRING_EVENT = {
    "exceptionId": "EX-1005",
    "transactionId": "TX-10006",
    "type": "AMOUNT_MISMATCH",
    "detectedAt": "2026-09-27T00:13:42.485226Z",
}


def test_a_spring_produced_event_deserialises() -> None:
    event = ReconciliationExceptionEvent.model_validate(SPRING_EVENT)

    assert event.exception_id == "EX-1005"
    assert event.transaction_id == "TX-10006"
    assert event.type is ExceptionType.AMOUNT_MISMATCH
    assert event.detected_at == datetime(2026, 9, 27, 0, 13, 42, 485226, tzinfo=UTC)


@pytest.mark.parametrize(
    "exception_type",
    ["MISSING_SETTLEMENT", "DUPLICATE_SETTLEMENT", "CURRENCY_MISMATCH", "AMOUNT_MISMATCH"],
)
def test_every_deterministic_exception_type_is_accepted(exception_type: str) -> None:
    event = ReconciliationExceptionEvent.model_validate({**SPRING_EVENT, "type": exception_type})

    assert event.type.value == exception_type


def test_the_enum_matches_the_financial_core_exactly() -> None:
    assert {member.value for member in ExceptionType} == {
        "MISSING_SETTLEMENT",
        "DUPLICATE_SETTLEMENT",
        "CURRENCY_MISMATCH",
        "AMOUNT_MISMATCH",
    }


def test_processor_fee_is_not_a_valid_exception_type() -> None:
    """It is a root-cause classification, never a detected discrepancy."""
    with pytest.raises(ValidationError):
        ReconciliationExceptionEvent.model_validate({**SPRING_EVENT, "type": "PROCESSOR_FEE"})


@pytest.mark.parametrize("field", ["exceptionId", "transactionId", "type", "detectedAt"])
def test_a_missing_required_field_is_rejected(field: str) -> None:
    payload = {key: value for key, value in SPRING_EVENT.items() if key != field}

    with pytest.raises(ValidationError):
        ReconciliationExceptionEvent.model_validate(payload)


def test_an_unknown_exception_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReconciliationExceptionEvent.model_validate({**SPRING_EVENT, "type": "SOMETHING_ELSE"})


def test_an_empty_identifier_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReconciliationExceptionEvent.model_validate({**SPRING_EVENT, "exceptionId": ""})


def test_an_unparseable_detected_at_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReconciliationExceptionEvent.model_validate({**SPRING_EVENT, "detectedAt": "not-a-timestamp"})


def test_a_timezone_naive_detected_at_is_rejected() -> None:
    """An instant with no offset is ambiguous; assuming UTC would be a guess."""
    with pytest.raises(ValidationError):
        ReconciliationExceptionEvent.model_validate(
            {**SPRING_EVENT, "detectedAt": "2026-09-27T00:13:42.485226"}
        )


def test_a_non_utc_offset_is_accepted_and_kept_aware() -> None:
    event = ReconciliationExceptionEvent.model_validate(
        {**SPRING_EVENT, "detectedAt": "2026-09-26T20:13:42.485226-04:00"}
    )

    assert event.detected_at.utcoffset() == timedelta(hours=-4)
    assert event.detected_at.astimezone(timezone.utc).hour == 0


@pytest.mark.parametrize("unexpected", ["correlationId", "eventId", "eventVersion", "eventType"])
def test_an_unexpected_field_is_rejected(unexpected: str) -> None:
    """The contract is exactly four fields, and both sides of it are owned here.

    Anything else means producer and consumer have drifted, which must surface
    rather than be tolerated. Extending the contract is a deliberate change made
    across the producer, this model, the tests and the documentation together.
    """
    with pytest.raises(ValidationError) as failure:
        ReconciliationExceptionEvent.model_validate({**SPRING_EVENT, unexpected: "anything"})

    assert failure.value.error_count() == 1
    error = failure.value.errors()[0]
    assert error["type"] == "extra_forbidden"
    assert error["loc"] == (unexpected,)


def test_several_unexpected_fields_are_all_reported() -> None:
    with pytest.raises(ValidationError) as failure:
        ReconciliationExceptionEvent.model_validate(
            {**SPRING_EVENT, "correlationId": "CORR-1", "eventVersion": "1"}
        )

    rejected = {error["loc"][0] for error in failure.value.errors()}
    assert rejected == {"correlationId", "eventVersion"}


def test_the_exact_producer_payload_remains_valid() -> None:
    """Strictness must reject drift without rejecting the current contract."""
    assert ReconciliationExceptionEvent.model_validate(SPRING_EVENT).exception_id == "EX-1005"


def test_the_event_is_immutable() -> None:
    event = ReconciliationExceptionEvent.model_validate(SPRING_EVENT)

    with pytest.raises(ValidationError):
        event.exception_id = "EX-9999"  # type: ignore[misc]


def test_the_model_carries_no_financial_evidence() -> None:
    """Identity only. Authoritative detail is fetched, never carried on the bus."""
    assert set(ReconciliationExceptionEvent.model_fields) == {
        "exception_id",
        "transaction_id",
        "type",
        "detected_at",
    }
