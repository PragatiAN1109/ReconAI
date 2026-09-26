package com.reconai.reconciliation;

import com.reconai.exception.ReconciliationException;

import java.time.Instant;
import java.util.List;

/**
 * The outcome of reconciling one transaction against its settlements.
 *
 * <p>V1 rule precedence yields at most one discrepancy per run, but the list shape
 * matches the API contract and leaves room for a later version that reports several.
 *
 * @param transactionId business ID of the transaction that was reconciled
 * @param reconciled    true when the authoritative records agree
 * @param exceptions    the discrepancy found, or empty when the records agree
 * @param reconciledAt  when the comparison ran
 */
public record ReconciliationResult(
        String transactionId,
        boolean reconciled,
        List<ReconciliationException> exceptions,
        Instant reconciledAt) {

    /** The records agree: no discrepancy, and nothing was persisted. */
    public static ReconciliationResult reconciled(String transactionId, Instant reconciledAt) {
        return new ReconciliationResult(transactionId, true, List.of(), reconciledAt);
    }

    /** The records disagree. The exception may be newly created or reused. */
    public static ReconciliationResult withException(String transactionId,
                                                     ReconciliationException exception,
                                                     Instant reconciledAt) {
        return new ReconciliationResult(transactionId, false, List.of(exception), reconciledAt);
    }
}
