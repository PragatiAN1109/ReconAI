package com.reconai.transaction.dto;

import com.reconai.common.money.Money;
import com.reconai.transaction.Transaction;
import com.reconai.transaction.TransactionStatus;
import com.reconai.transaction.TransactionType;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Response body for the transaction endpoints (docs/api-contract.md sections 6.1, 6.2).
 *
 * <p>The internal UUID is deliberately absent. Callers address transactions by business
 * identifier only, which keeps the database key private and free to change.
 *
 * <p>The contract shows {@code createdAt} on the create response and omits it on the
 * read response; it is included in both here, since an additional field is harmless to
 * a consumer and the two responses being the same shape is simpler to work with.
 */
public record TransactionResponse(
        String transactionId,
        String merchantId,
        BigDecimal amount,
        BigDecimal expectedSettlementAmount,
        String currency,
        TransactionType transactionType,
        TransactionStatus status,
        Instant transactionTimestamp,
        Instant createdAt) {

    public static TransactionResponse from(Transaction transaction) {
        return new TransactionResponse(
                transaction.getTransactionId(),
                transaction.getMerchantId(),
                Money.forResponse(transaction.getAmount()),
                Money.forResponse(transaction.getExpectedSettlementAmount()),
                transaction.getCurrency(),
                transaction.getTransactionType(),
                transaction.getStatus(),
                transaction.getTransactionTimestamp(),
                transaction.getCreatedAt());
    }
}
