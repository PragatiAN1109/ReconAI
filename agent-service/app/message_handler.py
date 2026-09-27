"""Decoding, validation and logging of one Kafka message.

Kept apart from the consumer loop so the whole decode-validate-log path can be
tested without Kafka, a broker, or an event loop.
"""

import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from app.events import ReconciliationExceptionEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordLocation:
    """Where a message sat in the log, for diagnosing a specific record."""

    topic: str
    partition: int
    offset: int


def handle_message(raw: bytes | None, location: RecordLocation) -> ReconciliationExceptionEvent | None:
    """Decode and validate one message, then log the outcome.

    Never raises. A message this service cannot understand is a fact about the
    message, not a reason to take the consumer down, so every failure is logged
    and reported by returning ``None``. The caller decides what that means for
    the offset.

    Phase 4.2 stops here: a valid event is logged and nothing else happens to
    it. No investigation is started, no data is fetched, no model is called.

    :return: the validated event, or ``None`` if the message was unusable
    """
    if raw is None:
        logger.error(
            "Discarding reconciliation exception event with no payload [topic=%s partition=%d offset=%d]",
            location.topic,
            location.partition,
            location.offset,
        )
        return None

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        logger.error(
            "Discarding reconciliation exception event that is not valid JSON "
            "[topic=%s partition=%d offset=%d error=%s]",
            location.topic,
            location.partition,
            location.offset,
            error,
        )
        return None

    if not isinstance(payload, dict):
        logger.error(
            "Discarding reconciliation exception event that is not a JSON object "
            "[topic=%s partition=%d offset=%d received=%s]",
            location.topic,
            location.partition,
            location.offset,
            type(payload).__name__,
        )
        return None

    try:
        event = ReconciliationExceptionEvent.model_validate(payload)
    except ValidationError as error:
        # error.errors() names the offending fields without echoing the whole
        # payload back into the log.
        logger.error(
            "Discarding reconciliation exception event that failed validation "
            "[topic=%s partition=%d offset=%d errors=%s]",
            location.topic,
            location.partition,
            location.offset,
            [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in error.errors()],
        )
        return None

    logger.info(
        "Received reconciliation exception "
        "[exception_id=%s transaction_id=%s exception_type=%s detected_at=%s "
        "topic=%s partition=%d offset=%d]",
        event.exception_id,
        event.transaction_id,
        event.type.value,
        event.detected_at.isoformat(),
        location.topic,
        location.partition,
        location.offset,
    )
    return event
