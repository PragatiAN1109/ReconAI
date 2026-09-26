package com.reconai.settlement.dto;

import com.reconai.settlement.SettlementStatus;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * Request body for {@code POST /api/v1/settlements} (docs/api-contract.md section 7.1).
 *
 * <p>The settlement identifier is allocated by the service, not supplied by the caller.
 */
public record CreateSettlementRequest(

        @NotBlank(message = "transactionId is required")
        @Size(max = 50, message = "transactionId must not exceed 50 characters")
        String transactionId,

        @NotBlank(message = "processor is required")
        @Size(max = 100, message = "processor must not exceed 100 characters")
        String processor,

        @NotNull(message = "settledAmount is required")
        @DecimalMin(value = "0.0", message = "settledAmount must be greater than or equal to 0")
        @Digits(integer = 15, fraction = 4,
                message = "settledAmount must have at most 15 integer digits and 4 decimal places")
        BigDecimal settledAmount,

        @NotBlank(message = "currency is required")
        @Pattern(regexp = "^[A-Za-z]{3}$",
                message = "currency must be exactly 3 characters, for example USD")
        String currency,

        @NotNull(message = "status is required")
        SettlementStatus status,

        @NotNull(message = "settlementTimestamp is required")
        Instant settlementTimestamp) {
}
