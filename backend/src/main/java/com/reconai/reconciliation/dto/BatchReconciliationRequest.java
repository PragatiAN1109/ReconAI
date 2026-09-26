package com.reconai.reconciliation.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;

import java.util.List;

/**
 * Request body for {@code POST /api/v1/reconciliation/run}
 * (docs/api-contract.md section 8.2).
 */
public record BatchReconciliationRequest(

        @NotEmpty(message = "transactionIds is required and must not be empty")
        List<@NotBlank(message = "transactionIds must not contain blank values") String>
                transactionIds) {
}
