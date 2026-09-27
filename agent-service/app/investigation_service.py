"""Creating, reusing and reading investigations."""

import logging
from dataclasses import dataclass

from sqlalchemy import Sequence, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Database
from app.events import ReconciliationExceptionEvent
from app.models import SCHEMA, Investigation, InvestigationStatus

logger = logging.getLogger(__name__)

# Business identifiers come from a PostgreSQL sequence, the same approach the
# financial core uses for TX-, SET- and EX- identifiers. A sequence is safe
# across concurrent workers and process restarts, which an in-memory counter is
# not. Allocation is non-transactional, so a rolled-back or conflicting insert
# leaves a gap in the numbering; uniqueness matters here and contiguity does
# not.
BUSINESS_ID_SEQUENCE = Sequence("investigation_business_id_seq", schema=SCHEMA)
BUSINESS_ID_PREFIX = "INV-"


@dataclass(frozen=True)
class InvestigationRecord:
    """The result of recording an event, and whether it was new.

    ``created`` is what distinguishes a first delivery from a duplicate. The
    caller needs it for logging; correctness does not depend on it, because
    either outcome leaves exactly one investigation.
    """

    investigation: Investigation
    created: bool


class InvestigationService:
    """Application logic for investigations.

    Reads and writes only the ``investigation`` schema. It never touches
    transactions, settlements or reconciliation exceptions: those belong to the
    financial core, and the identifiers stored here are references to be
    resolved later through its API, not join keys.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_or_get(self, event: ReconciliationExceptionEvent) -> InvestigationRecord:
        """Record an investigation for this exception, or return the existing one.

        At-least-once delivery means the same event can arrive more than once,
        and two deliveries can be in flight at the same time. A read followed by
        a write would let both pass the read and both insert, so the insert
        itself resolves the race: the unique constraint on ``exception_id``
        decides, and the loser reads the winner's row.

        :return: the investigation and whether this call created it
        """
        async with self._database.session() as session:
            existing = await self._find_by_exception_id(session, event.exception_id)
            if existing is not None:
                logger.info(
                    "Reusing existing investigation [investigation_id=%s exception_id=%s]",
                    existing.investigation_id,
                    existing.exception_id,
                )
                return InvestigationRecord(investigation=existing, created=False)

            investigation_id = await self._next_investigation_id(session)
            statement = (
                insert(Investigation)
                .values(
                    investigation_id=investigation_id,
                    exception_id=event.exception_id,
                    transaction_id=event.transaction_id,
                    exception_type=event.type.value,
                    status=InvestigationStatus.PENDING.value,
                    detected_at=event.detected_at,
                )
                # A concurrent delivery may have inserted between the read above
                # and this write. Doing nothing on conflict lets that happen
                # without raising, leaving the transaction usable.
                .on_conflict_do_nothing(index_elements=["exception_id"])
                .returning(Investigation)
            )
            inserted = (await session.execute(statement)).scalar_one_or_none()

            if inserted is None:
                # The conflict fired: another delivery won. Its row is the
                # answer, and the identifier allocated above becomes a gap.
                winner = await self._find_by_exception_id(session, event.exception_id)
                if winner is None:  # pragma: no cover - would mean the row vanished
                    raise RuntimeError(
                        f"Investigation for {event.exception_id} neither inserted nor found"
                    )
                logger.info(
                    "Concurrent delivery already created this investigation; reusing it "
                    "[investigation_id=%s exception_id=%s discarded_id=%s]",
                    winner.investigation_id,
                    winner.exception_id,
                    investigation_id,
                )
                return InvestigationRecord(investigation=winner, created=False)

            logger.info(
                "Created investigation [investigation_id=%s exception_id=%s transaction_id=%s "
                "exception_type=%s status=%s]",
                inserted.investigation_id,
                inserted.exception_id,
                inserted.transaction_id,
                inserted.exception_type,
                inserted.status,
            )
            return InvestigationRecord(investigation=inserted, created=True)

    async def get_by_investigation_id(self, investigation_id: str) -> Investigation | None:
        async with self._database.session() as session:
            return await session.scalar(
                select(Investigation).where(Investigation.investigation_id == investigation_id)
            )

    async def list_all(self) -> list[Investigation]:
        """Every investigation, newest first.

        Unpaginated on purpose: the table holds one row per detected
        discrepancy, and adding pagination before there is a client that needs
        it would be guessing at the shape.
        """
        async with self._database.session() as session:
            result = await session.scalars(
                select(Investigation).order_by(
                    Investigation.created_at.desc(), Investigation.investigation_id.asc()
                )
            )
            return list(result)

    @staticmethod
    async def _find_by_exception_id(
        session: AsyncSession, exception_id: str
    ) -> Investigation | None:
        return await session.scalar(
            select(Investigation).where(Investigation.exception_id == exception_id)
        )

    @staticmethod
    async def _next_investigation_id(session: AsyncSession) -> str:
        value = await session.scalar(BUSINESS_ID_SEQUENCE.next_value().select())
        return f"{BUSINESS_ID_PREFIX}{value}"
