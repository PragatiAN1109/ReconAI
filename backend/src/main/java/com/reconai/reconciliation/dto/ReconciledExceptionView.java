package com.reconai.reconciliation.dto;

import com.reconai.common.money.Money;
import com.reconai.exception.ExceptionStatus;
import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationException;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * A discrepancy as reported inside a reconciliation response
 * (docs/api-contract.md section 8.1).
 *
 * <p>Deliberately narrower than {@code ExceptionResponse}: the transaction ID is
 * already on the enclosing response, and the detection timestamp is the enclosing
 * {@code reconciledAt}. Callers wanting the full record fetch it from
 * {@code GET /api/v1/exceptions/{exceptionId}}.
 */
public record ReconciledExceptionView(
        String exceptionId,
        ExceptionType exceptionType,
        String expectedValue,
        String observedValue,
        BigDecimal differenceAmount,
        String currency,
        ExceptionStatus status) {

    public static ReconciledExceptionView from(ReconciliationException exception) {
        return new ReconciledExceptionView(
                exception.getExceptionId(),
                exception.getExceptionType(),
                exception.getExpectedValue(),
                exception.getObservedValue(),
                Money.forResponse(exception.getDifferenceAmount()),
                exception.getCurrency(),
                exception.getStatus());
    }
}
