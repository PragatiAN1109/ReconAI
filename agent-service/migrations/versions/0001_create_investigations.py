"""Create the investigation schema, sequence and investigations table.

Revision ID: 0001
Revises:
Create Date: 2026-09-27

Creates only structures this service owns. The financial core's transactions,
settlements and reconciliation_exceptions tables live in "public" in the same
database and are not referenced, constrained or touched here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "investigation"


def upgrade() -> None:
    # A dedicated schema makes the ownership boundary something the database
    # enforces rather than something a reviewer has to take on trust.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # Business identifiers come from a sequence, matching how the financial
    # core allocates TX-, SET- and EX- identifiers. Safe across concurrent
    # workers and restarts; allocation is non-transactional, so a rolled-back
    # or conflicting insert leaves a gap, which is fine.
    op.execute(f"CREATE SEQUENCE {SCHEMA}.investigation_business_id_seq START WITH 1001 INCREMENT BY 1")

    op.create_table(
        "investigations",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("investigation_id", sa.String(50), nullable=False),
        # References to financial-core records. Deliberately plain columns: a
        # foreign key here would couple this service's writes to another
        # service's schema.
        sa.Column("exception_id", sa.String(50), nullable=False),
        sa.Column("transaction_id", sa.String(50), nullable=False),
        sa.Column("exception_type", sa.String(50), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default=sa.text("'PENDING'")
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_investigations"),
        sa.UniqueConstraint("investigation_id", name="uq_investigations_investigation_id"),
        # The idempotency guarantee. One investigation per reconciliation
        # exception, enforced by the database because duplicate Kafka
        # deliveries can arrive concurrently and an application-level check
        # would let both through.
        sa.UniqueConstraint("exception_id", name="uq_investigations_exception_id"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'ESCALATED')",
            name="ck_investigations_status",
        ),
        # Mirrors the financial core's own constraint. PROCESSOR_FEE is absent
        # from both: it is a root-cause classification, never a detected
        # discrepancy.
        sa.CheckConstraint(
            "exception_type IN ('AMOUNT_MISMATCH', 'MISSING_SETTLEMENT', "
            "'DUPLICATE_SETTLEMENT', 'CURRENCY_MISMATCH')",
            name="ck_investigations_exception_type",
        ),
        schema=SCHEMA,
    )

    # docs/data-model.md section 21. investigation_id and exception_id are
    # already indexed by their unique constraints.
    op.create_index(
        "idx_investigations_status", "investigations", ["status"], schema=SCHEMA
    )
    op.create_index(
        "idx_investigations_transaction_id", "investigations", ["transaction_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("idx_investigations_transaction_id", "investigations", schema=SCHEMA)
    op.drop_index("idx_investigations_status", "investigations", schema=SCHEMA)
    op.drop_table("investigations", schema=SCHEMA)
    op.execute(f"DROP SEQUENCE IF EXISTS {SCHEMA}.investigation_business_id_seq")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
