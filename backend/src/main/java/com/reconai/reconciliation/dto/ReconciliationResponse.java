package com.reconai.reconciliation.dto;

import com.reconai.reconciliation.ReconciliationResult;

import java.time.Instant;
import java.util.List;

/**
 * Response body for reconciling a single transaction
 * (docs/api-contract.md section 8.1).
 *
 * <p>The call returns as soon as the deterministic comparison is done. It never waits
 * on, or triggers, any AI investigation.
 */
public record ReconciliationResponse(
        String transactionId,
        boolean reconciled,
        List<ReconciledExceptionView> exceptions,
        Instant reconciledAt) {

    public static ReconciliationResponse from(ReconciliationResult result) {
        return new ReconciliationResponse(
                result.transactionId(),
                result.reconciled(),
                result.exceptions().stream().map(ReconciledExceptionView::from).toList(),
                result.reconciledAt());
    }
}
