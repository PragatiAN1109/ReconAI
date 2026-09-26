-- ReconAI schema V3: reconciliation_exceptions
-- Source of truth: docs/data-model.md section 5.
--
-- A reconciliation exception records WHAT is inconsistent between authoritative
-- financial records. It never records WHY the inconsistency occurred: root-cause
-- classification belongs to the investigation layer implemented in a later phase.

CREATE TABLE reconciliation_exceptions (
    id                UUID           NOT NULL,
    exception_id      VARCHAR(50)    NOT NULL,
    transaction_id    VARCHAR(50)    NOT NULL,
    settlement_id     VARCHAR(50),
    exception_type    VARCHAR(50)    NOT NULL,
    expected_value    VARCHAR(255),
    observed_value    VARCHAR(255),
    difference_amount NUMERIC(19, 4),
    currency          VARCHAR(3),
    status            VARCHAR(30)    NOT NULL,
    detected_at       TIMESTAMPTZ    NOT NULL,
    created_at        TIMESTAMPTZ    NOT NULL,
    updated_at        TIMESTAMPTZ    NOT NULL,

    CONSTRAINT pk_reconciliation_exceptions PRIMARY KEY (id),
    CONSTRAINT uq_reconciliation_exceptions_exception_id UNIQUE (exception_id),

    CONSTRAINT fk_reconciliation_exceptions_transaction
        FOREIGN KEY (transaction_id) REFERENCES transactions (transaction_id),

    -- Nullable: MISSING_SETTLEMENT has no settlement to reference, and
    -- DUPLICATE_SETTLEMENT concerns a set of settlements rather than a single one.
    CONSTRAINT fk_reconciliation_exceptions_settlement
        FOREIGN KEY (settlement_id) REFERENCES settlements (settlement_id),

    -- ARCHITECTURAL INVARIANT (docs/data-model.md section 20, "Detection/Investigation
    -- Separation"). Only deterministic discrepancy types are storable here.
    --
    -- PROCESSOR_FEE is deliberately absent. It is a root-cause classification produced
    -- by AI investigation, not a discrepancy the financial core can detect. The
    -- deterministic engine establishes only that two authoritative records disagree;
    -- it cannot know why. This constraint makes that invariant structurally
    -- unviolable rather than a convention an application defect could breach.
    -- Introducing a new deterministic type requires a deliberate migration.
    CONSTRAINT ck_reconciliation_exceptions_type
        CHECK (exception_type IN (
            'AMOUNT_MISMATCH',
            'MISSING_SETTLEMENT',
            'DUPLICATE_SETTLEMENT',
            'CURRENCY_MISMATCH'
        )),

    CONSTRAINT ck_reconciliation_exceptions_status
        CHECK (status IN (
            'OPEN',
            'INVESTIGATING',
            'AWAITING_REVIEW',
            'RESOLVED',
            'ESCALATED'
        )),

    CONSTRAINT ck_reconciliation_exceptions_currency_length
        CHECK (currency IS NULL OR char_length(currency) = 3)
);

-- docs/data-model.md section 21, plus exception_type to support the documented
-- GET /api/v1/exceptions filters.
CREATE INDEX idx_reconciliation_exceptions_transaction_id
    ON reconciliation_exceptions (transaction_id);
CREATE INDEX idx_reconciliation_exceptions_status
    ON reconciliation_exceptions (status);
CREATE INDEX idx_reconciliation_exceptions_exception_type
    ON reconciliation_exceptions (exception_type);

-- ---------------------------------------------------------------------------
-- IDEMPOTENCY
-- ---------------------------------------------------------------------------
-- Re-running reconciliation over unchanged financial records must not accumulate
-- duplicate unresolved exceptions (EX-1042, EX-1043, EX-1044, ...).
--
-- Strategy: an exception's identity is its NATURAL KEY, composed only of columns
-- already defined by the data model -- no synthetic fingerprint column is added:
--
--     (transaction_id, exception_type, settlement_id, expected_value, observed_value)
--
-- Before inserting, the reconciliation service looks for an existing UNRESOLVED
-- exception with the same natural key and reuses it. This partial unique index is
-- the database-level backstop for that check.
--
-- Per exception type the natural key carries the relevant settlement context:
--
--   MISSING_SETTLEMENT     settlement_id NULL
--                          expected_value 'SETTLEMENT_PRESENT'
--                          observed_value 'NO_SETTLEMENT'
--
--   DUPLICATE_SETTLEMENT   settlement_id NULL
--                          expected_value '1_COMPLETED_SETTLEMENT'
--                          observed_value sorted, comma-separated settlement IDs
--
--   CURRENCY_MISMATCH      settlement_id set; expected/observed are currency codes
--
--   AMOUNT_MISMATCH        settlement_id set; expected/observed are scale-normalised
--                          decimal strings
--
-- Consequences, all intended:
--   * Re-running reconciliation on unchanged records is a no-op.
--   * A genuinely changed discrepancy (a corrected settled amount, a third duplicate
--     settlement) produces a different natural key and therefore a new exception.
--   * The index is scoped to status <> 'RESOLVED', so a resolved exception does not
--     suppress re-detection if the same discrepancy recurs later.
--
-- COALESCE is required because NULLs are not equal to one another in a unique index
-- and would otherwise defeat the constraint for nullable columns.
CREATE UNIQUE INDEX ux_reconciliation_exceptions_unresolved_natural_key
    ON reconciliation_exceptions (
        transaction_id,
        exception_type,
        COALESCE(settlement_id, ''),
        COALESCE(expected_value, ''),
        COALESCE(observed_value, '')
    )
    WHERE status <> 'RESOLVED';

-- Allocates readable business identifiers: EX-1001, EX-1002, ...
CREATE SEQUENCE exception_business_id_seq START WITH 1001 INCREMENT BY 1;
