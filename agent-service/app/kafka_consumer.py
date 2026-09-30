"""Kafka consumption of reconciliation exceptions.

Owns the consumer's lifecycle, its loop and its offsets. It knows nothing about
HTTP, and the FastAPI layer knows nothing about Kafka beyond starting it,
stopping it and asking whether it is running.
"""

import asyncio
import logging

from aiokafka import AIOKafkaConsumer

from app.config import Settings
from app.investigation_service import InvestigationRecord, InvestigationService
from app.message_handler import RecordLocation, handle_message
from app.processing import ProcessingOutcome

logger = logging.getLogger(__name__)


class PersistenceUnavailableError(RuntimeError):
    """A valid event could not be recorded, so its offset must not be committed."""


class ReconciliationExceptionConsumer:
    """Consumes ``reconciliation.exceptions`` and records an investigation for each.

    **Consumer group.** Fixed, from configuration. A stable group means offsets
    survive restarts and several instances share the partitions rather than each
    receiving every event. A generated group id would replay or duplicate work
    on every restart.

    **Offset reset.** ``latest``. A brand-new group starts from events produced
    from now on, rather than replaying the entire development history of the
    topic on first connection. The practical consequence is that this consumer
    must already be running before an event is produced, or it will not see it.

    **Commits.** Auto-commit is off. A record is committed only once it has been
    durably recorded, or once it has been judged permanently unusable. Delivery
    is therefore **at-least-once**: a crash between the database commit and the
    offset commit replays the record, and the unique ``exception_id`` makes that
    replay reuse the existing investigation instead of creating a second one.

    The database commit and the Kafka commit are two separate transactions and
    are **not** atomic. That window is real, and idempotency is what makes it
    harmless rather than a duplicate investigation.
    """

    def __init__(
        self,
        settings: Settings,
        investigation_service: InvestigationService,
        runner: object | None = None,
    ) -> None:
        self._settings = settings
        self._investigations = investigation_service
        # Optional. Absent when no model is configured or automatic
        # investigation is switched off, in which case consuming an event
        # records a PENDING investigation and stops — the behaviour this
        # service had before automation existed.
        self._runner = runner
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

        # Drained after the loop stops, so nothing new is scheduled while we
        # wait, and before the connection closes so a finishing investigation
        # still has the resources it was started with.
        if self._runner is not None:
            await self._runner.drain(self._settings.auto_investigate_drain_seconds)

        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            logger.info("Kafka consumer stopped [topic=%s]", self._settings.kafka_exceptions_topic)

    async def process_record(self, raw: bytes | None, location: RecordLocation) -> ProcessingOutcome:
        """Turn one record into an investigation, and report what to do with the offset.

        Exposed separately from the loop so the decision — commit, skip or stop
        — can be tested without Kafka.

        When automatic investigation is enabled this also *schedules* the
        investigation, but it never waits for it. The returned outcome — and so
        the offset commit — depends only on whether the investigation was
        durably recorded. Provider latency, provider outages and investigation
        failures cannot influence Kafka acknowledgement, and cannot stop the
        loop.
        """
        event = handle_message(raw, location)
        if event is None:
            return ProcessingOutcome.INVALID

        try:
            record = await self._investigations.create_or_get(event)
        except Exception:
            logger.exception(
                "Could not record investigation for a valid event; its offset will not be "
                "committed and it will be redelivered "
                "[exception_id=%s topic=%s partition=%d offset=%d]",
                event.exception_id,
                location.topic,
                location.partition,
                location.offset,
            )
            return ProcessingOutcome.RETRY_LATER

        self._schedule_investigation(record)
        return ProcessingOutcome.PROCESSED

    def _schedule_investigation(self, record: InvestigationRecord) -> None:
        """Hand a newly recorded investigation to the background runner.

        Only for investigations this call actually created. A redelivered event
        returns an existing row, and that row has either been investigated, is
        being investigated, or has already reached a terminal state — so
        scheduling it again would at best be refused by the workflow's
        conditional claim and at worst duplicate a paid call if that claim ever
        weakened. Not scheduling is the cheaper guarantee.

        Never raises. A scheduling problem must not turn into an uncommitted
        offset for an investigation that was recorded successfully.
        """
        if self._runner is None:
            return
        if not record.created:
            logger.info(
                "Not scheduling an automatic investigation for a redelivered event "
                "[investigation_id=%s exception_id=%s]",
                record.investigation.investigation_id,
                record.investigation.exception_id,
            )
            return

        try:
            self._runner.schedule(record.investigation.investigation_id)
            logger.info(
                "Automatic investigation scheduled [investigation_id=%s exception_id=%s]",
                record.investigation.investigation_id,
                record.investigation.exception_id,
            )
        except Exception:
            logger.exception(
                "Could not schedule an automatic investigation; the investigation remains "
                "PENDING [investigation_id=%s]",
                record.investigation.investigation_id,
            )

    async def _consume(self) -> None:
        """Handle records until cancelled, or until a record cannot be recorded."""
        assert self._consumer is not None

        try:
            async for record in self._consumer:
                location = RecordLocation(
                    topic=record.topic, partition=record.partition, offset=record.offset
                )
                outcome = await self.process_record(record.value, location)

                if outcome is ProcessingOutcome.RETRY_LATER:
                    # Committing here would acknowledge this record, because a
                    # commit covers everything up to the current position. So
                    # consumption stops instead. Readiness reports the instance
                    # as not ready, and on restart the record is redelivered
                    # from the last committed offset.
                    raise PersistenceUnavailableError(
                        f"Stopping consumption at {location.topic}-{location.partition}"
                        f"@{location.offset}: the event was valid but could not be recorded"
                    )

                await self._consumer.commit()
        except asyncio.CancelledError:
            logger.debug("Kafka consumer loop cancelled")
            raise
        except Exception:
            # The loop is finished, so readiness will report this instance as
            # unable to do its job.
            logger.exception("Kafka consumer loop stopped unexpectedly")
            raise
