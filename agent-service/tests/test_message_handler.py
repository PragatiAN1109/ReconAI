"""Tests for decoding, validating and logging one Kafka message."""

import json
import logging

import pytest

from app.events import ExceptionType
from app.message_handler import RecordLocation, handle_message

LOCATION = RecordLocation(topic="reconciliation.exceptions", partition=0, offset=42)

SPRING_EVENT = {
    "exceptionId": "EX-1005",
    "transactionId": "TX-10006",
    "type": "AMOUNT_MISMATCH",
    "detectedAt": "2026-09-27T00:13:42.485226Z",
}


def encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode()


def test_a_valid_message_returns_the_validated_event() -> None:
    event = handle_message(encode(SPRING_EVENT), LOCATION)

    assert event is not None
    assert event.exception_id == "EX-1005"
    assert event.type is ExceptionType.AMOUNT_MISMATCH


def test_a_valid_message_is_logged_with_the_fields_needed_to_trace_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.message_handler"):
        handle_message(encode(SPRING_EVENT), LOCATION)

    logged = caplog.text
    assert "exception_id=EX-1005" in logged
    assert "transaction_id=TX-10006" in logged
    assert "exception_type=AMOUNT_MISMATCH" in logged
    assert "detected_at=2026-09-27T00:13:42.485226+00:00" in logged
    assert "topic=reconciliation.exceptions" in logged
    assert "partition=0" in logged
    assert "offset=42" in logged


# ---------------------------------------------------------------------------
# Unusable messages are reported, never raised
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("malformed json", b"{not json"),
        ("empty payload", b""),
        ("json array", b"[1, 2, 3]"),
        ("json string", b'"just a string"'),
        ("json null", b"null"),
        ("invalid utf-8", b"\xff\xfe\x00"),
        ("no payload at all", None),
    ],
)
def test_an_undecodable_message_is_skipped_without_raising(name: str, raw: bytes | None) -> None:
    assert handle_message(raw, LOCATION) is None


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("missing exceptionId", {k: v for k, v in SPRING_EVENT.items() if k != "exceptionId"}),
        ("missing transactionId", {k: v for k, v in SPRING_EVENT.items() if k != "transactionId"}),
        ("missing type", {k: v for k, v in SPRING_EVENT.items() if k != "type"}),
        ("missing detectedAt", {k: v for k, v in SPRING_EVENT.items() if k != "detectedAt"}),
        ("unknown type", {**SPRING_EVENT, "type": "PROCESSOR_FEE"}),
        ("unparseable detectedAt", {**SPRING_EVENT, "detectedAt": "yesterday"}),
        ("naive detectedAt", {**SPRING_EVENT, "detectedAt": "2026-09-27T00:13:42"}),
        ("empty exceptionId", {**SPRING_EVENT, "exceptionId": ""}),
        ("unexpected field", {**SPRING_EVENT, "correlationId": "CORR-1"}),
    ],
)
def test_a_schema_invalid_message_is_skipped_without_raising(
    name: str, payload: dict[str, object]
) -> None:
    assert handle_message(encode(payload), LOCATION) is None


def test_an_invalid_message_is_logged_with_enough_detail_to_diagnose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {**SPRING_EVENT, "type": "PROCESSOR_FEE"}

    with caplog.at_level(logging.ERROR, logger="app.message_handler"):
        handle_message(encode(payload), LOCATION)

    logged = caplog.text
    assert "failed validation" in logged
    assert "offset=42" in logged
    assert "type" in logged


def test_malformed_json_is_logged_as_a_json_problem(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="app.message_handler"):
        handle_message(b"{not json", LOCATION)

    assert "not valid JSON" in caplog.text


def test_processing_continues_after_an_invalid_message() -> None:
    """One bad record must not poison the ones behind it."""
    assert handle_message(b"{not json", LOCATION) is None

    event = handle_message(encode(SPRING_EVENT), LOCATION)

    assert event is not None
    assert event.exception_id == "EX-1005"


def test_an_unexpected_field_makes_the_message_invalid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contract drift is an invalid event, handled like any other."""
    payload = {**SPRING_EVENT, "correlationId": "CORR-89123"}

    with caplog.at_level(logging.ERROR, logger="app.message_handler"):
        event = handle_message(encode(payload), LOCATION)

    assert event is None
    assert "failed validation" in caplog.text
    assert "correlationId" in caplog.text
    assert "offset=42" in caplog.text


def test_processing_continues_after_a_message_with_an_unexpected_field() -> None:
    """A drifted producer must not stall the consumer on valid events behind it."""
    assert handle_message(encode({**SPRING_EVENT, "eventVersion": "2"}), LOCATION) is None

    event = handle_message(encode(SPRING_EVENT), LOCATION)

    assert event is not None
    assert event.exception_id == "EX-1005"


def test_a_clean_event_produces_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.message_handler"):
        handle_message(encode(SPRING_EVENT), LOCATION)

    assert caplog.text == ""
