package com.reconai.settlement;

/**
 * Status reported by the processor for a settlement (docs/data-model.md section 4).
 *
 * <p>These values are mirrored by the {@code ck_settlements_status} check constraint,
 * so adding a value here requires a Flyway migration.
 */
public enum SettlementStatus {
    PENDING,
    COMPLETED,
    REVERSED,
    FAILED
}
