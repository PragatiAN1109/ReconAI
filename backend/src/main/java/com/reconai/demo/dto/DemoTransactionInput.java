package com.reconai.demo.dto;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.math.BigDecimal;

/**
 * The transaction side of a demo reconciliation run.
 *
 * <p>Only the three financially meaningful values are accepted. Everything else a
 * {@code Transaction} needs — its identifier, merchant, type, status and timestamp — is
 * supplied by the server, so a caller cannot choose an identifier, backdate a record or
 * claim a status the demo does not model.
 *
 * <p>Bounds here are deliberately tighter than the domain's. The financial core accepts
 * {@code Digits(15, 4)}; this endpoint is public and unauthenticated, so it accepts two
 * decimal places and a ceiling that keeps a demo recognisably a demo. Two places also
 * keeps every value safely inside {@link com.reconai.common.money.Money#STORAGE_SCALE},
 * which rounds with {@code UNNECESSARY} and would otherwise throw on a value it cannot
 * represent exactly.
 */
public record DemoTransactionInput(

        @NotNull(message = "transaction.amount is required")
        @DecimalMin(value = "0.00", message = "transaction.amount must not be negative")
        @DecimalMax(value = "1000000.00", message = "transaction.amount must not exceed 1000000.00")
        @Digits(integer = 7, fraction = 2,
                message = "transaction.amount must have at most 2 decimal places")
        BigDecimal amount,

        @NotNull(message = "transaction.expectedSettlementAmount is required")
        @DecimalMin(value = "0.00",
                message = "transaction.expectedSettlementAmount must not be negative")
        @DecimalMax(value = "1000000.00",
                message = "transaction.expectedSettlementAmount must not exceed 1000000.00")
        @Digits(integer = 7, fraction = 2,
                message = "transaction.expectedSettlementAmount must have at most 2 decimal places")
        BigDecimal expectedSettlementAmount,

        @NotBlank(message = "transaction.currency is required")
        @Pattern(regexp = "(?i)^(USD|EUR|GBP)$",
                message = "transaction.currency must be one of USD, EUR or GBP")
        String currency) {
}
