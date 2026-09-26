package com.reconai.exception.dto;

import com.reconai.common.money.Money;
import com.reconai.exception.ExceptionStatus;
import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationException;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Response body for a reconciliation exception (docs/api-contract.md sections 9.1, 9.2).
 *
 * <p>The internal UUID is deliberately absent; callers address exceptions by business
 * identifier only.
 *
 * <p>The contract's list example omits expectedValue and observedValue, which the detail
 * example includes. One record serves both endpoints: the extra fields are useful in a
 * queue view and never wrong, matching the approach already taken for settlements.
 *
 * <p>expectedValue and observedValue are strings while differenceAmount is a number.
 * That is the contract as written in both docs/api-contract.md and docs/data-model.md,
 * and it is deliberate: the two value columns also carry non-monetary content such as
 * currency codes and settlement ID lists, whereas the difference is always money.
 */
public record ExceptionResponse(
        String exceptionId,
        String transactionId,
        String settlementId,
        ExceptionType exceptionType,
        String expectedValue,
        String observedValue,
        BigDecimal differenceAmount,
        String currency,
        ExceptionStatus status,
        Instant detectedAt) {

    public static ExceptionResponse from(ReconciliationException exception) {
        return new ExceptionResponse(
                exception.getExceptionId(),
                exception.getTransactionId(),
                exception.getSettlementId(),
                exception.getExceptionType(),
                exception.getExpectedValue(),
                exception.getObservedValue(),
                Money.forResponse(exception.getDifferenceAmount()),
                exception.getCurrency(),
                exception.getStatus(),
                exception.getDetectedAt());
    }
}
