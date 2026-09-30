package com.reconai.demo.dto;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

import java.math.BigDecimal;

/**
 * One settlement in a demo reconciliation run.
 *
 * <p>As with the transaction, only the values reconciliation actually compares are
 * accepted. The identifier, processor, status and timestamp are set by the server.
 *
 * <p>Status in particular is never a caller's choice: reconciliation considers only
 * {@code COMPLETED} settlements, so letting a caller send {@code PENDING} would produce a
 * {@code MISSING_SETTLEMENT} for a settlement that is visibly present in the response and
 * make the engine look wrong. An absent settlement is expressed by omitting it from the
 * list, which is unambiguous.
 */
public record DemoSettlementInput(

        @NotNull(message = "settlements[].settledAmount is required")
        @DecimalMin(value = "0.00", message = "settlements[].settledAmount must not be negative")
        @DecimalMax(value = "1000000.00",
                message = "settlements[].settledAmount must not exceed 1000000.00")
        @Digits(integer = 7, fraction = 2,
                message = "settlements[].settledAmount must have at most 2 decimal places")
        BigDecimal settledAmount,

        @NotBlank(message = "settlements[].currency is required")
        @Pattern(regexp = "(?i)^(USD|EUR|GBP)$",
                message = "settlements[].currency must be one of USD, EUR or GBP")
        String currency) {
}
