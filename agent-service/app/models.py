"""Persistence model for investigations.

This service owns the ``investigation`` schema and nothing else. Transactions,
settlements and reconciliation exceptions belong to the financial core, and are
referenced here only by their business identifiers — never by foreign key, and
never read directly from this service.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, MetaData, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
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

    Kafka ingestion creates ``PENDING``. Running the investigation claims it
    into ``RUNNING``, and the outcome decides what follows: a grounded,
    confident result reaches ``AWAITING_REVIEW``; anything weaker reaches
    ``ESCALATED``; an execution failure reaches ``FAILED``. A human approving
    moves it to ``COMPLETED``.

    Every transition is made by application code. The model proposes an
    explanation and never decides what state the workflow is in.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    #: A grounded, high-confidence result is waiting for a human.
    AWAITING_REVIEW = "AWAITING_REVIEW"
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


class ReviewDecision(StrEnum):
    """A human's verdict on an AI recommendation.

    Reviewing means "a human looked at this explanation and judged it". It does
    not mean any money moved: nothing in this service can move money.
    """

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class ActorType(StrEnum):
    """Who caused an audited event."""

    SYSTEM = "SYSTEM"
    AI = "AI"
    HUMAN = "HUMAN"


class AuditEventType(StrEnum):
    """The events worth reconstructing months later.

    Deliberately few. An audit trail nobody can read is not an audit trail.
    """

    INVESTIGATION_CREATED = "INVESTIGATION_CREATED"
    INVESTIGATION_STARTED = "INVESTIGATION_STARTED"
    AI_RESULT_GENERATED = "AI_RESULT_GENERATED"
    INVESTIGATION_AWAITING_REVIEW = "INVESTIGATION_AWAITING_REVIEW"
    INVESTIGATION_ESCALATED = "INVESTIGATION_ESCALATED"
    INVESTIGATION_FAILED = "INVESTIGATION_FAILED"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    REVIEW_REJECTED = "REVIEW_REJECTED"


class Recommendation(Base):
    """The durable AI explanation for one investigation.

    Stored relationally rather than as an opaque blob so the fields that matter
    — what was concluded, how confidently — are queryable and constrained.

    At most one per investigation, enforced by a unique constraint. A second
    recommendation would make "the AI's conclusion" ambiguous.

    Deliberately absent: the prompt, the provider's raw request or response, and
    anything resembling model reasoning. Only the user-visible structured result
    and operational metadata are kept.
    """

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    recommendation_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    investigation_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)

    classification: Mapped[str] = mapped_column(String(50), nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    # NUMERIC rather than float: a stored confidence should read back as what
    # was written, and the 0..1 bound is a database constraint.
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    requires_human_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    # Traceability: enough to know what produced this, nothing more.
    model_provider: Mapped[str | None] = mapped_column(String(50))
    model_name: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RecommendationEvidence(Base):
    """One evidence reference behind a recommendation.

    Only references that passed grounding validation reach this table. An
    unverifiable citation is never persisted, so the question "what evidence did
    the AI actually use?" has an answer that can be trusted later.
    """

    __tablename__ = "recommendation_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    recommendation_id: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    #: A financial-core or policy business identifier. Not a foreign key: those
    #: records belong to another service.
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    #: A short excerpt for policy evidence, so a reviewer can see what was cited
    #: without re-running a search against a corpus that may have changed.
    excerpt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Review(Base):
    """A human's decision on a recommendation.

    At most one per investigation: a decision that can be overwritten is not a
    decision. A second attempt does not silently replace the first.

    ``reviewed_by`` is **caller-supplied and unauthenticated**. There is no
    authentication in this service, so this is demo attribution, not identity.
    """

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    review_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    investigation_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    recommendation_id: Mapped[str] = mapped_column(String(50), nullable=False)

    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AuditEvent(Base):
    """An append-only record of something that happened to an investigation.

    There is no update or delete path, in the service or the API. An audit trail
    that can be edited proves nothing.

    ``metadata_json`` holds a small non-sensitive summary — a classification, a
    confidence, a failure category. Never a prompt, a provider payload, a
    credential, or model reasoning.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    #: The sequence value behind ``event_id``, kept as an integer to give the
    #: trail a total order. Ordering by ``occurred_at`` alone is not enough:
    #: several events are committed in one transaction and would tie, and
    #: ordering by ``event_id`` would sort "AUD-10001" before "AUD-9001".
    sequence_no: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    investigation_id: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB)
    #: clock_timestamp(), not now(): an audit entry should say when the event
    #: happened, not when its transaction began.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
