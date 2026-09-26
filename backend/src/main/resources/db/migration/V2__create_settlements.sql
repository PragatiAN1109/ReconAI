-- ReconAI schema V2: settlements
-- Source of truth: docs/data-model.md section 4.
--
-- A settlement is the authoritative record reported by an external processor.
--
-- Cardinality is Transaction 1 ---- 0..* Settlement. There is deliberately NO unique
-- constraint on transaction_id: duplicate settlement detection requires that multiple
-- settlements for one transaction be storable. A constraint here would make the
-- DUPLICATE_SETTLEMENT reconciliation rule impossible to exercise.

CREATE TABLE settlements (
    id                   UUID           NOT NULL,
    settlement_id        VARCHAR(50)    NOT NULL,
    transaction_id       VARCHAR(50)    NOT NULL,
    processor            VARCHAR(100)   NOT NULL,
    settled_amount       NUMERIC(19, 4) NOT NULL,
    currency             VARCHAR(3)     NOT NULL,
    status               VARCHAR(30)    NOT NULL,
    settlement_timestamp TIMESTAMPTZ    NOT NULL,
    created_at           TIMESTAMPTZ    NOT NULL,

    CONSTRAINT pk_settlements PRIMARY KEY (id),
    CONSTRAINT uq_settlements_settlement_id UNIQUE (settlement_id),

    -- docs/data-model.md section 4: "V1 may associate records using the transaction
    -- business identifier." The reference targets transactions.transaction_id rather
    -- than the internal UUID, which the unique constraint in V1 makes possible.
    CONSTRAINT fk_settlements_transaction
        FOREIGN KEY (transaction_id) REFERENCES transactions (transaction_id),

    CONSTRAINT ck_settlements_settled_amount_non_negative
        CHECK (settled_amount >= 0),
    CONSTRAINT ck_settlements_currency_length
        CHECK (char_length(currency) = 3),
    CONSTRAINT ck_settlements_status
        CHECK (status IN ('PENDING', 'COMPLETED', 'REVERSED', 'FAILED'))
);

-- docs/data-model.md section 21. Reconciliation loads every settlement for a
-- transaction on each run, so this index is on the engine's hot path.
CREATE INDEX idx_settlements_transaction_id ON settlements (transaction_id);

-- Allocates readable business identifiers: SET-8001, SET-8002, ...
CREATE SEQUENCE settlement_business_id_seq START WITH 8001 INCREMENT BY 1;
