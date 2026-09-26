package com.reconai.exception;

/**
 * The deterministic discrepancy types the financial core can detect
 * (docs/data-model.md section 5).
 *
 * <p>Each value states WHAT disagrees between two authoritative records. None of them
 * states WHY, because comparing a transaction to a settlement cannot establish a cause.
 *
 * <p>PROCESSOR_FEE is deliberately absent and must stay absent. It is a root-cause
 * classification produced by investigation: an AMOUNT_MISMATCH may later be explained
 * as a processor fee, but the deterministic engine only ever sees two numbers that
 * differ. Adding it here would collapse the detection/investigation boundary the whole
 * architecture rests on.
 *
 * <p>These values are mirrored by the {@code ck_reconciliation_exceptions_type} check
 * constraint, so adding a value here requires a Flyway migration.
 */
public enum ExceptionType {
    AMOUNT_MISMATCH,
    MISSING_SETTLEMENT,
    DUPLICATE_SETTLEMENT,
    CURRENCY_MISMATCH
}
