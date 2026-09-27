package com.reconai.feerule;

/**
 * Classification of a fee (docs/data-model.md section 8).
 *
 * <p>Mirrored by the {@code ck_fee_rules_fee_type} check constraint, so adding a value
 * here requires a Flyway migration.
 */
public enum FeeType {
    FIXED,
    PERCENTAGE,
    NETWORK,
    CROSS_BORDER,
    PROCESSING
}
