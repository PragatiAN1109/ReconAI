package com.reconai.transaction;

/**
 * Classification of a transaction (docs/data-model.md section 3).
 *
 * <p>These values are mirrored by the {@code ck_transactions_transaction_type} check
 * constraint, so adding a value here requires a Flyway migration.
 */
public enum TransactionType {
    PURCHASE,
    REFUND,
    REVERSAL
}
