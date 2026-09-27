"""Persistence model for investigations.

This service owns the ``investigation`` schema and nothing else. Transactions,
settlements and reconciliation exceptions belong to the financial core, and are
referenced here only by their business identifiers — never by foreign key, and
never read directly from this service.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, MetaData, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "investigation"

# Every Python-owned table lives in its own schema. The financial core's tables
# sit in "public" in the same database, so a dedicated schema turns the
# ownership boundary from a convention into something the database enforces and
# an auditor can verify.
metadata = MetaData(schema=SCHEMA)


class Base(DeclarativeBase):
    metadata = metadata


class InvestigationStatus(StrEnum):
    """Lifecycle of an investigation (docs/data-model.md section 6).

    Kafka ingestion only ever creates ``PENDING``. There is no agent yet, so
    nothing legitimately moves an investigation beyond it; pretending otherwise
    would be inventing a result.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class Investigation(Base):
    """An investigation of one reconciliation exception.

    Created from a Kafka event, at most once per ``exception_id``. That limit is
    a unique constraint in the database rather than an application check,
    because duplicate deliveries can arrive concurrently.
    """

    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    investigation_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    # Business identifiers owned by the financial core. Deliberately not foreign
    # keys: this service must not depend on, or constrain, another service's
    # tables.
    exception_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(50), nullable=False)

    # Stored as text rather than a database enum so that a new deterministic
    # exception type on the producer side does not require a type migration
    # here before events can be recorded.
    exception_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text(f"'{InvestigationStatus.PENDING}'")
    )

    # When the financial core detected the discrepancy, carried from the event,
    # as distinct from when this service happened to record it.
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return (
            f"Investigation(investigation_id={self.investigation_id!r}, "
            f"exception_id={self.exception_id!r}, status={self.status!r})"
        )
