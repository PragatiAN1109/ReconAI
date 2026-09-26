package com.reconai.reconciliation.dto;

/**
 * Response body for {@code POST /api/v1/reconciliation/run}
 * (docs/api-contract.md section 8.2).
 *
 * <p>The contract's shape is kept verbatim, including the 202 status and the wording
 * "Reconciliation started." V1 executes synchronously, so the work is already finished
 * when this is returned; results are read back from the exception APIs.
 */
public record BatchReconciliationResponse(int requested, String message) {

    private static final String MESSAGE = "Reconciliation started.";

    public static BatchReconciliationResponse of(int requested) {
        return new BatchReconciliationResponse(requested, MESSAGE);
    }
}
