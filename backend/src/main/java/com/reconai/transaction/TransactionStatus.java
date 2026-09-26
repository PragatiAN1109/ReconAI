package com.reconai.transaction;

/**
 * Lifecycle status of a transaction (docs/data-model.md section 3).
 *
 * <p>These values are mirrored by the {@code ck_transactions_status} check constraint,
 * so adding a value here requires a Flyway migration.
 */
public enum TransactionStatus {
    AUTHORIZED,
    POSTED,
    SETTLED,
    REVERSED
}
