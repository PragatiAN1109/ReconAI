"""Add recommendations, evidence, reviews and audit events.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Everything created here lives in the "investigation" schema, which this service
owns. The financial core's tables are in "public" in the same database and are
neither referenced nor constrained: the references to its records are plain
string columns holding business identifiers.

Also widens the investigation status constraint to admit AWAITING_REVIEW, the
state between "the AI has produced a grounded result" and "a human has decided".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "investigation"


def upgrade() -> None:
    # Separate sequences per concept, so identifier ranges stay independent and
    # readable. Allocation is non-transactional: a rolled-back insert leaves a
    # gap, which is expected and harmless.
    for sequence, start in (
        ("recommendation_business_id_seq", 3001),
        ("review_business_id_seq", 7001),
        ("audit_event_business_id_seq", 9001),
    ):
        op.execute(f"CREATE SEQUENCE {SCHEMA}.{sequence} START WITH {start} INCREMENT BY 1")

    # AWAITING_REVIEW is the whole point of the phase: a result exists, and
    # nothing proceeds until a human decides. The constraint is replaced rather
    # than dropped, so an unknown status is still rejected by the database.
    op.execute(
        f"ALTER TABLE {SCHEMA}.investigations DROP CONSTRAINT ck_investigations_status"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.investigations ADD CONSTRAINT ck_investigations_status "
        "CHECK (status IN ('PENDING', 'RUNNING', 'AWAITING_REVIEW', 'COMPLETED', "
        "'FAILED', 'ESCALATED'))"
    )

    op.create_table(
        "recommendations",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recommendation_id", sa.String(50), nullable=False),
        sa.Column("investigation_id", sa.String(50), nullable=False),
        sa.Column("classification", sa.String(50), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        # NUMERIC, never a float: a confidence must read back as what was
        # written. Four decimal places is more precision than a model's
        # self-reported number deserves, and costs nothing.
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column(
            "requires_human_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("model_provider", sa.String(50), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recommendations"),
        sa.UniqueConstraint("recommendation_id", name="uq_recommendations_recommendation_id"),
        # One recommendation per investigation. Enforced here rather than in
        # application code because two concurrent runs of the same
        # investigation would otherwise both insert, leaving no answer to
        # "what did the AI conclude?".
        sa.UniqueConstraint("investigation_id", name="uq_recommendations_investigation_id"),
        # PROCESSOR_FEE belongs here and nowhere in the financial core's
        # exception types: it is a conclusion about a cause, not a detected
        # discrepancy. The two vocabularies are deliberately different, and the
        # database enforces both.
        sa.CheckConstraint(
            "classification IN ('PROCESSOR_FEE', 'PROCESSOR_DELAY', 'DUPLICATE_PROCESSING', "
            "'CURRENCY_CONVERSION', 'PROCESSOR_ERROR', 'UNKNOWN', 'INSUFFICIENT_EVIDENCE')",
            name="ck_recommendations_classification",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_recommendations_confidence"
        ),
        # A recommendation that did not require human approval would be an
        # autonomous decision. The database refuses to store one.
        sa.CheckConstraint(
            "requires_human_approval = true",
            name="ck_recommendations_requires_human_approval",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "recommendation_evidence",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("recommendation_id", sa.String(50), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        # A transaction, settlement, fee-rule or policy identifier. Not a
        # foreign key: those records belong to the financial core or to the
        # policy corpus on disk.
        sa.Column("reference", sa.String(255), nullable=False),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recommendation_evidence"),
        # This one *is* a foreign key: both tables are ours, and evidence
        # without its recommendation is meaningless. Cascading delete keeps the
        # pair consistent if a recommendation is ever removed.
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            [f"{SCHEMA}.recommendations.recommendation_id"],
            name="fk_recommendation_evidence_recommendation",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "source_type IN ('TRANSACTION', 'SETTLEMENT', 'FEE_RULE', 'POLICY_DOCUMENT')",
            name="ck_recommendation_evidence_source_type",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_recommendation_evidence_recommendation_id",
        "recommendation_evidence",
        ["recommendation_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "reviews",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("review_id", sa.String(50), nullable=False),
        sa.Column("investigation_id", sa.String(50), nullable=False),
        sa.Column("recommendation_id", sa.String(50), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        # Caller-supplied and unauthenticated. There is no authentication in
        # this service, so this column records a claim, not an identity.
        sa.Column("reviewed_by", sa.String(255), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.UniqueConstraint("review_id", name="uq_reviews_review_id"),
        # One decision per investigation. A second reviewer cannot quietly
        # overwrite the first; the database is what makes that true under
        # concurrent requests.
        sa.UniqueConstraint("investigation_id", name="uq_reviews_investigation_id"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            [f"{SCHEMA}.recommendations.recommendation_id"],
            name="fk_reviews_recommendation",
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'ESCALATED')", name="ck_reviews_decision"
        ),
        sa.CheckConstraint("length(trim(reviewed_by)) > 0", name="ck_reviews_reviewed_by"),
        schema=SCHEMA,
    )

    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", sa.String(50), nullable=False),
        # The sequence value behind event_id, as an integer. Ordering the trail
        # by occurred_at alone ties when several events share a transaction,
        # and ordering by event_id would put "AUD-10001" before "AUD-9001".
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("investigation_id", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_type", sa.String(30), nullable=False),
        # Null for SYSTEM and AI events. For HUMAN events this is the same
        # unauthenticated, caller-supplied string as reviews.reviewed_by.
        sa.Column("actor_id", sa.String(255), nullable=True),
        # A small non-sensitive summary: a classification, a confidence, a
        # failure category. Never a prompt, a provider payload, a credential, or
        # anything resembling model reasoning.
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        # clock_timestamp(), not now(): an audit entry should record when the
        # event happened, not when its transaction started.
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.UniqueConstraint("event_id", name="uq_audit_events_event_id"),
        sa.UniqueConstraint("sequence_no", name="uq_audit_events_sequence_no"),
        sa.CheckConstraint(
            "actor_type IN ('SYSTEM', 'AI', 'HUMAN')", name="ck_audit_events_actor_type"
        ),
        schema=SCHEMA,
    )
    # Audit events are written once and read as a per-investigation timeline,
    # so that is what the index serves.
    op.create_index(
        "idx_audit_events_investigation_id",
        "audit_events",
        ["investigation_id", "sequence_no"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("idx_audit_events_investigation_id", "audit_events", schema=SCHEMA)
    op.drop_table("audit_events", schema=SCHEMA)
    op.drop_table("reviews", schema=SCHEMA)
    op.drop_index(
        "idx_recommendation_evidence_recommendation_id",
        "recommendation_evidence",
        schema=SCHEMA,
    )
    op.drop_table("recommendation_evidence", schema=SCHEMA)
    op.drop_table("recommendations", schema=SCHEMA)

    # Any investigation left in AWAITING_REVIEW would violate the narrower
    # constraint being restored, so the downgrade must decide what becomes of
    # it. ESCALATED is the honest answer: its result is being dropped, and it
    # needs a human.
    op.execute(
        f"UPDATE {SCHEMA}.investigations SET status = 'ESCALATED' "
        "WHERE status = 'AWAITING_REVIEW'"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.investigations DROP CONSTRAINT ck_investigations_status"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.investigations ADD CONSTRAINT ck_investigations_status "
        "CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'ESCALATED'))"
    )

    for sequence in (
        "audit_event_business_id_seq",
        "review_business_id_seq",
        "recommendation_business_id_seq",
    ):
        op.execute(f"DROP SEQUENCE IF EXISTS {SCHEMA}.{sequence}")
