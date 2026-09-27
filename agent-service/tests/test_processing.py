"""Tests for what consuming a record concludes, and what it means for the offset.

The Kafka boundary is faked. What is under test is the decision — commit, skip,
or stop — not the broker.
"""

import json
import logging

import pytest

from app.config import Settings
from app.events import ReconciliationExceptionEvent
from app.kafka_consumer import ReconciliationExceptionConsumer
from app.message_handler import RecordLocation
from app.processing import ProcessingOutcome

LOCATION = RecordLocation(topic="reconciliation.exceptions", partition=0, offset=42)

SPRING_EVENT = {
    "exceptionId": "EX-1006",
    "transactionId": "TX-10007",
    "type": "AMOUNT_MISMATCH",
    "detectedAt": "2026-09-27T02:04:16.954772Z",
}


class RecordingInvestigationService:
    """Captures what the consumer asked it to record."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[ReconciliationExceptionEvent] = []

    async def create_or_get(self, event: ReconciliationExceptionEvent) -> object:
        if self.fail:
            raise ConnectionError("simulated database unavailable")
        self.events.append(event)
        return object()


def consumer_with(service: RecordingInvestigationService) -> ReconciliationExceptionConsumer:
    return ReconciliationExceptionConsumer(Settings(_env_file=None), service)  # type: ignore[arg-type]


def encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# A valid event that is recorded may be committed
# ---------------------------------------------------------------------------


async def test_a_valid_recorded_event_is_safe_to_commit() -> None:
    service = RecordingInvestigationService()

    outcome = await consumer_with(service).process_record(encode(SPRING_EVENT), LOCATION)

    assert outcome is ProcessingOutcome.PROCESSED
    assert len(service.events) == 1
    assert service.events[0].exception_id == "EX-1006"


async def test_the_validated_event_is_what_reaches_persistence() -> None:
    service = RecordingInvestigationService()

    await consumer_with(service).process_record(encode(SPRING_EVENT), LOCATION)

    event = service.events[0]
    assert event.transaction_id == "TX-10007"
    assert event.type.value == "AMOUNT_MISMATCH"
    assert event.detected_at.tzinfo is not None


# ---------------------------------------------------------------------------
# An unusable message is skipped, as in Phase 4.2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("malformed json", b"{not json"),
        ("missing field", b'{"exceptionId": "EX-1"}'),
        ("unknown type", json.dumps({**SPRING_EVENT, "type": "PROCESSOR_FEE"}).encode()),
        ("naive timestamp", json.dumps({**SPRING_EVENT, "detectedAt": "2026-09-27T00:00:00"}).encode()),
        ("unexpected field", json.dumps({**SPRING_EVENT, "correlationId": "C-1"}).encode()),
    ],
)
async def test_an_unusable_message_is_skipped_and_never_persisted(name: str, raw: bytes) -> None:
    service = RecordingInvestigationService()

    outcome = await consumer_with(service).process_record(raw, LOCATION)

    assert outcome is ProcessingOutcome.INVALID
    assert service.events == []


# ---------------------------------------------------------------------------
# A valid event that cannot be recorded must not be acknowledged
# ---------------------------------------------------------------------------


async def test_a_persistence_failure_is_not_treated_as_processed() -> None:
    """A valid financial exception must not be lost because storage was down."""
    service = RecordingInvestigationService(fail=True)

    outcome = await consumer_with(service).process_record(encode(SPRING_EVENT), LOCATION)

    assert outcome is ProcessingOutcome.RETRY_LATER
    assert outcome is not ProcessingOutcome.INVALID
    assert outcome is not ProcessingOutcome.PROCESSED


async def test_a_persistence_failure_is_logged_with_the_offset_that_will_be_redelivered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = RecordingInvestigationService(fail=True)

    with caplog.at_level(logging.ERROR, logger="app.kafka_consumer"):
        await consumer_with(service).process_record(encode(SPRING_EVENT), LOCATION)

    logged = caplog.text
    assert "will not be committed" in logged
    assert "exception_id=EX-1006" in logged
    assert "offset=42" in logged


async def test_a_storage_outage_is_distinguished_from_a_bad_message() -> None:
    """The two failures look similar and must be handled oppositely."""
    working = RecordingInvestigationService()
    broken = RecordingInvestigationService(fail=True)

    bad_message = await consumer_with(working).process_record(b"{not json", LOCATION)
    storage_outage = await consumer_with(broken).process_record(encode(SPRING_EVENT), LOCATION)

    # A bad message will never become valid, so it is committed and skipped.
    assert bad_message is ProcessingOutcome.INVALID
    # A valid event outlives the outage, so its offset stays uncommitted.
    assert storage_outage is ProcessingOutcome.RETRY_LATER


def test_every_outcome_has_a_defined_offset_meaning() -> None:
    assert {outcome.name for outcome in ProcessingOutcome} == {
        "PROCESSED",
        "INVALID",
        "RETRY_LATER",
    }
