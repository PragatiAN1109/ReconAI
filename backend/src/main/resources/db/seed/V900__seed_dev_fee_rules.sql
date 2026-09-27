-- Development fee rules. Applied only under the "dev" profile, which is the only
-- profile that adds classpath:db/seed to the Flyway locations.
--
-- SYNTHETIC DATA. These are invented for demonstration and resemble no real
-- processor's, bank's or network's published fee schedule.
--
-- FR-14 exists to make one specific demonstration possible. The reconciliation demo
-- has a transaction expecting 2500.00 USD against a settlement of 2450.00 USD, which
-- the financial core reports as AMOUNT_MISMATCH with a difference of 50.00. A fee rule
-- of 50.00 USD on the same processor is the evidence an investigation would need to
-- explain that difference.
--
-- It is evidence and nothing more. Reconciliation does not read this table, and no
-- component concludes from a matching amount that this fee was actually charged.

INSERT INTO fee_rules (id, rule_id, merchant_id, processor, fee_type, fee_amount, currency, description, active, created_at)
VALUES
    -- The rule that matches the 50.00 demo difference exactly.
    ('a1e1c1d1-0000-4000-8000-000000000014', 'FR-14', 'MERCHANT-PHASE43-DEMO', 'NORTHSTAR_PAYMENTS',
     'PROCESSING', 50.0000, 'USD',
     'Cross-network settlement processing fee applied per settled purchase.',
     TRUE, now()),

    -- Applies to every merchant on the processor: merchant_id is null. Present so that
    -- a merchant-filtered query is exercised against both kinds of rule.
    ('a1e1c1d1-0000-4000-8000-000000000015', 'FR-15', NULL, 'NORTHSTAR_PAYMENTS',
     'NETWORK', 2.5000, 'USD',
     'Per-settlement network access fee applied to all merchants on this processor.',
     TRUE, now()),

    -- A different processor, so processor filtering has something to exclude.
    ('a1e1c1d1-0000-4000-8000-000000000016', 'FR-16', NULL, 'ATLAS_CLEARING',
     'PROCESSING', 35.0000, 'USD',
     'Standard settlement processing fee.',
     TRUE, now()),

    -- Inactive, and deliberately the same shape and amount as FR-14. A query that
    -- ignores the active flag would return a fee that is no longer charged, which is
    -- exactly the sort of stale evidence an investigation must not be handed.
    ('a1e1c1d1-0000-4000-8000-000000000017', 'FR-17', 'MERCHANT-PHASE43-DEMO', 'NORTHSTAR_PAYMENTS',
     'PROCESSING', 50.0000, 'USD',
     'Withdrawn cross-network processing fee. Superseded by FR-14.',
     FALSE, now()),

    -- A non-USD rule, so currency filtering has something to exclude.
    ('a1e1c1d1-0000-4000-8000-000000000018', 'FR-18', NULL, 'NORTHSTAR_PAYMENTS',
     'CROSS_BORDER', 12.0000, 'EUR',
     'Cross-border settlement handling fee for euro-denominated settlements.',
     TRUE, now());
