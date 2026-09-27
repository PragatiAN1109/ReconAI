"""Kafka consumption of reconciliation exceptions.

Owns the consumer's lifecycle and its loop. It knows nothing about HTTP, and
the FastAPI layer knows nothing about Kafka beyond starting it, stopping it and
asking whether it is running.
"""

import asyncio
import logging

from aiokafka import AIOKafkaConsumer

from app.config import Settings
from app.message_handler import RecordLocation, handle_message

logger = logging.getLogger(__name__)


class ReconciliationExceptionConsumer:
    """Consumes ``reconciliation.exceptions`` and validates what it finds.

    **Consumer group.** Fixed, from configuration. A stable group means offsets
    survive restarts and several instances share the partitions rather than each
    receiving every event. A generated group id would replay or duplicate work
    on every restart.

    **Offset reset.** ``latest``. A brand-new group starts from events produced
    from now on, rather than replaying the entire development history of the
    topic on first connection. The practical consequence is that this consumer
    must already be running before an event is produced, or it will not see it.

    **Commits.** Auto-commit is off and each record is committed after it has
    been handled. Delivery is therefore **at-least-once**: a crash between
    handling and committing replays the record. In this phase replay only
    repeats a log line, but investigation handling in a later phase must be
    idempotent by ``exceptionId``.

    **Unusable records.** A record that cannot be decoded or validated is logged
    and then *committed anyway*, which skips it. Without a dead-letter topic —
    deliberately out of scope here — not committing would make the consumer
    re-read the same poison record forever and stall the partition behind it.
    The trade-off is that such a record is dropped, and the log line is the only
    remaining trace of it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """True when the consumer started and its loop is still alive."""
        return self._consumer is not None and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Connect, subscribe and begin consuming.

        Raises if the broker cannot be reached, so the caller can decide whether
        that is fatal.
        """
        consumer = AIOKafkaConsumer(
            self._settings.kafka_exceptions_topic,
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            group_id=self._settings.kafka_consumer_group,
            auto_offset_reset="latest",
            enable_auto_commit=False,
        )
        await consumer.start()
        self._consumer = consumer
        self._task = asyncio.create_task(self._consume(), name="reconciliation-exception-consumer")

        logger.info(
            "Kafka consumer started [topic=%s group=%s bootstrap_servers=%s auto_offset_reset=latest]",
            self._settings.kafka_exceptions_topic,
            self._settings.kafka_consumer_group,
            self._settings.kafka_bootstrap_servers,
        )

    async def stop(self) -> None:
        """Stop the loop and close the connection.

        Safe to call whether or not startup succeeded.
        """
        if self._task is not None:
            self._task.cancel()
            # Waiting for the cancellation to be observed is what keeps the task
            # from outliving the application.
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            logger.info("Kafka consumer stopped [topic=%s]", self._settings.kafka_exceptions_topic)

    async def _consume(self) -> None:
        """Handle records until cancelled."""
        assert self._consumer is not None

        try:
            async for record in self._consumer:
                location = RecordLocation(
                    topic=record.topic, partition=record.partition, offset=record.offset
                )
                try:
                    handle_message(record.value, location)
                except Exception:
                    # handle_message is written not to raise. If it ever does,
                    # one bad record still must not take down consumption.
                    logger.exception(
                        "Unexpected failure handling record [topic=%s partition=%d offset=%d]",
                        location.topic,
                        location.partition,
                        location.offset,
                    )
                # Committed whatever the outcome; see the class docstring on why
                # an unusable record is skipped rather than retried forever.
                await self._consumer.commit()
        except asyncio.CancelledError:
            logger.debug("Kafka consumer loop cancelled")
            raise
        except Exception:
            # The loop is finished, so readiness will report this instance as
            # unable to do its job.
            logger.exception("Kafka consumer loop stopped unexpectedly")
            raise
