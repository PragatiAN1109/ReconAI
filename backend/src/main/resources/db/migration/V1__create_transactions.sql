-- ReconAI schema V1: transactions
-- Source of truth: docs/data-model.md section 3.
--
-- A transaction is an authoritative internal record describing what the financial
-- system expects to be settled. It is never derived from, or modified by, AI output.
--
-- Monetary columns use NUMERIC(19,4). Floating point types are prohibited.
-- Timestamps use TIMESTAMPTZ and are stored in UTC.

CREATE TABLE transactions (
    id                         UUID          NOT NULL,
    transaction_id             VARCHAR(50)   NOT NULL,
    merchant_id                VARCHAR(50)   NOT NULL,
    amount                     NUMERIC(19, 4) NOT NULL,
    expected_settlement_amount NUMERIC(19, 4) NOT NULL,
    currency                   VARCHAR(3)    NOT NULL,
    transaction_type           VARCHAR(30)   NOT NULL,
    status                     VARCHAR(30)   NOT NULL,
    transaction_timestamp      TIMESTAMPTZ   NOT NULL,
    created_at                 TIMESTAMPTZ   NOT NULL,
    updated_at                 TIMESTAMPTZ   NOT NULL,

    CONSTRAINT pk_transactions PRIMARY KEY (id),

    -- The readable business identifier (e.g. TX-48291) is unique and is the value
    -- other tables reference. It is kept separate from the internal UUID key.
    CONSTRAINT uq_transactions_transaction_id UNIQUE (transaction_id),

    CONSTRAINT ck_transactions_amount_non_negative
        CHECK (amount >= 0),
    CONSTRAINT ck_transactions_expected_settlement_non_negative
        CHECK (expected_settlement_amount >= 0),
    CONSTRAINT ck_transactions_currency_length
        CHECK (char_length(currency) = 3),

    -- Enum-backed columns are constrained at the database level so that an
    -- application defect cannot persist a value outside the documented domain.
    CONSTRAINT ck_transactions_transaction_type
        CHECK (transaction_type IN ('PURCHASE', 'REFUND', 'REVERSAL')),
    CONSTRAINT ck_transactions_status
        CHECK (status IN ('AUTHORIZED', 'POSTED', 'SETTLED', 'REVERSED'))
);

-- docs/data-model.md section 21. transactions(transaction_id) is already indexed
-- by the unique constraint above.
CREATE INDEX idx_transactions_merchant_id ON transactions (merchant_id);

-- Allocates readable business identifiers: TX-10001, TX-10002, ...
CREATE SEQUENCE transaction_business_id_seq START WITH 10001 INCREMENT BY 1;
