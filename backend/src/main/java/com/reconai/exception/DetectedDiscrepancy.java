package com.reconai.exception;

import java.math.BigDecimal;

/**
 * A discrepancy that some caller has already decided exists, described in the form this
 * package persists.
 *
 * <p>This record carries a decision; it never makes one. Nothing in this package
 * inspects transactions or settlements or works out which {@link ExceptionType} applies.
 * The reconciliation engine owns that judgement and hands the result here.
 *
 * <p>The five fields after the type form the natural key used for idempotency, together
 * with the transaction and type. The conventions below keep that key stable across
 * repeated reconciliation of unchanged records, so they must be applied consistently:
 *
 * <pre>
 * MISSING_SETTLEMENT     settlementId    null
 *                        expectedValue   "SETTLEMENT_PRESENT"
 *                        observedValue   "NO_SETTLEMENT"
 *
 * DUPLICATE_SETTLEMENT   settlementId    null
 *                        expectedValue   "1_COMPLETED_SETTLEMENT"
 *                        observedValue   sorted, comma-separated completed settlement IDs
 *
 * CURRENCY_MISMATCH      settlementId    the settlement concerned
 *                        expectedValue   transaction currency
 *                        observedValue   settlement currency
 *
 * AMOUNT_MISMATCH        settlementId    the settlement concerned
 *                        expectedValue   expected settlement amount
 *                        observedValue   actual settled amount
 *                        difference      expectedSettlementAmount - settledAmount
 * </pre>
 *
 * @param transactionId    business ID of the transaction the discrepancy concerns
 * @param exceptionType    which deterministic discrepancy was observed
 * @param settlementId     relevant settlement business ID, or null
 * @param expectedValue    what the transaction record led us to expect, or null
 * @param observedValue    what the settlement records showed, or null
 * @param differenceAmount expected minus observed where monetary, otherwise null
 * @param currency         currency of the difference, or null
 */
public record DetectedDiscrepancy(
        String transactionId,
        ExceptionType exceptionType,
        String settlementId,
        String expectedValue,
        String observedValue,
        BigDecimal differenceAmount,
        String currency) {
}
