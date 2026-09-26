package com.reconai.transaction.dto;

import com.reconai.transaction.TransactionStatus;
import com.reconai.transaction.TransactionType;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Request body for {@code POST /api/v1/transactions} (docs/api-contract.md section 6.1).
 *
 * <p>The business identifier is not accepted from the caller: it is allocated by the
 * service so that identifiers stay unique and under the financial core's control.
 */
public record CreateTransactionRequest(

        @NotBlank(message = "merchantId is required")
        @Size(max = 50, message = "merchantId must not exceed 50 characters")
        String merchantId,

        @NotNull(message = "amount is required")
        @DecimalMin(value = "0.0", message = "amount must be greater than or equal to 0")
        @Digits(integer = 15, fraction = 4,
                message = "amount must have at most 15 integer digits and 4 decimal places")
        BigDecimal amount,

        @NotNull(message = "expectedSettlementAmount is required")
        @DecimalMin(value = "0.0", message = "expectedSettlementAmount must be greater than or equal to 0")
        @Digits(integer = 15, fraction = 4,
                message = "expectedSettlementAmount must have at most 15 integer digits and 4 decimal places")
        BigDecimal expectedSettlementAmount,

        @NotBlank(message = "currency is required")
        @Pattern(regexp = "^[A-Za-z]{3}$",
                message = "currency must be exactly 3 characters, for example USD")
        String currency,

        @NotNull(message = "transactionType is required")
        TransactionType transactionType,

        @NotNull(message = "status is required")
        TransactionStatus status,

        @NotNull(message = "transactionTimestamp is required")
        Instant transactionTimestamp) {
}
