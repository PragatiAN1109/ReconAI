-- ReconAI schema V4: fee_rules
-- Source of truth: docs/data-model.md section 8.
--
-- Structured fee configuration, held separately from policy documents because it is
-- operational configuration rather than prose.
--
-- A fee rule is EVIDENCE. Its existence says a fee of this shape applies to this
-- processor and merchant. It never says that a particular transaction was charged one:
-- concluding that is investigation's job, and it needs more than this table.

CREATE TABLE fee_rules (
    id          UUID           NOT NULL,
    rule_id     VARCHAR(50)    NOT NULL,

    -- Nullable: a rule with no merchant applies to every merchant on that processor.
    merchant_id VARCHAR(50),
    processor   VARCHAR(100)   NOT NULL,
    fee_type    VARCHAR(50)    NOT NULL,

    -- V1 models fixed-amount fees only, so these are NOT NULL. The documented model
    -- allows a percentage rule instead, which is why it makes both nullable; adding
    -- fee_percentage later means relaxing these two columns.
    fee_amount  NUMERIC(19, 4) NOT NULL,
    currency    VARCHAR(3)     NOT NULL,

    description TEXT           NOT NULL,
    active      BOOLEAN        NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ    NOT NULL,

    CONSTRAINT pk_fee_rules PRIMARY KEY (id),

    -- The readable business identifier (e.g. FR-14) is unique and is what evidence
    -- and later citations refer to.
    CONSTRAINT uq_fee_rules_rule_id UNIQUE (rule_id),

    CONSTRAINT ck_fee_rules_fee_amount_non_negative
        CHECK (fee_amount >= 0),
    CONSTRAINT ck_fee_rules_currency_length
        CHECK (char_length(currency) = 3),
    CONSTRAINT ck_fee_rules_fee_type
        CHECK (fee_type IN ('FIXED', 'PERCENTAGE', 'NETWORK', 'CROSS_BORDER', 'PROCESSING'))
);

-- docs/data-model.md section 21. rule_id is already indexed by its unique constraint.
CREATE INDEX idx_fee_rules_merchant_id ON fee_rules (merchant_id);
CREATE INDEX idx_fee_rules_processor ON fee_rules (processor);
