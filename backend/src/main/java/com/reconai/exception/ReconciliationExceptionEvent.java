package com.reconai.exception;

import java.time.Instant;

/**
 * Announces that a new reconciliation exception has been detected.
 *
 * <p>Used twice with the same shape: raised in-process when the exception row is
 * created, then forwarded verbatim to Kafka once the database transaction commits. It
 * is therefore a published contract — a future investigation consumer parses exactly
 * these four fields.
 *
 * <p>The payload is deliberately nothing but identity and classification. It carries no
 * internal UUID, no settlement, no amounts, no merchant, and nothing from the
 * investigation layer such as root cause, recommendation or confidence. A consumer that
 * needs authoritative detail fetches it through the controlled read APIs, so this event
 * never becomes a second, stale copy of financial data travelling over a message bus.
 *
 * @param exceptionId   business ID of the exception, for example EX-1042
 * @param transactionId business ID of the transaction it concerns
 * @param type          which deterministic discrepancy was detected
 * @param detectedAt    when detection happened, ISO-8601 UTC
 */
public record ReconciliationExceptionEvent(
        String exceptionId,
        String transactionId,
        ExceptionType type,
        Instant detectedAt) {

    public static ReconciliationExceptionEvent from(ReconciliationException exception) {
        return new ReconciliationExceptionEvent(
                exception.getExceptionId(),
                exception.getTransactionId(),
                exception.getExceptionType(),
                exception.getDetectedAt());
    }
}
