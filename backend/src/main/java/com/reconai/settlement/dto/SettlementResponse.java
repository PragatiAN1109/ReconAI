package com.reconai.settlement.dto;

import com.reconai.common.money.Money;
import com.reconai.settlement.Settlement;
import com.reconai.settlement.SettlementStatus;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Response body for a single settlement (docs/api-contract.md section 7.1).
 *
 * <p>The internal UUID is deliberately absent; callers address settlements by business
 * identifier only.
 *
 * <p>The contract shows {@code transactionId} on the create response and omits it from
 * the objects nested inside the list response, where the parent already carries it. The
 * field is kept in both here so one record serves both endpoints; a repeated identifier
 * is redundant for a consumer but never wrong.
 */
public record SettlementResponse(
        String settlementId,
        String transactionId,
        String processor,
        BigDecimal settledAmount,
        String currency,
        SettlementStatus status,
        Instant settlementTimestamp) {

    public static SettlementResponse from(Settlement settlement) {
        return new SettlementResponse(
                settlement.getSettlementId(),
                settlement.getTransactionId(),
                settlement.getProcessor(),
                Money.forResponse(settlement.getSettledAmount()),
                settlement.getCurrency(),
                settlement.getStatus(),
                settlement.getSettlementTimestamp());
    }
}
