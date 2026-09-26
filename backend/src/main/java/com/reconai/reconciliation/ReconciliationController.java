package com.reconai.reconciliation;

import com.reconai.reconciliation.dto.BatchReconciliationRequest;
import com.reconai.reconciliation.dto.BatchReconciliationResponse;
import com.reconai.reconciliation.dto.ReconciliationResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Reconciliation endpoints (docs/api-contract.md section 8).
 *
 * <p>The request body carries no instructions and no configuration. Reconciliation is
 * driven entirely by the authoritative records already stored, which is what makes the
 * result reproducible.
 */
@RestController
@RequestMapping("/api/v1/reconciliation")
public class ReconciliationController {

    private final ReconciliationService reconciliationService;

    public ReconciliationController(ReconciliationService reconciliationService) {
        this.reconciliationService = reconciliationService;
    }

    /** Reconciles one transaction and returns the deterministic outcome. */
    @PostMapping("/transactions/{transactionId}")
    public ReconciliationResponse reconcile(@PathVariable String transactionId) {
        return ReconciliationResponse.from(reconciliationService.reconcile(transactionId));
    }

    /**
     * Reconciles several transactions.
     *
     * <p>Returns 202 with the body the contract specifies. The work is synchronous and
     * already complete when this returns; findings are read from
     * {@code GET /api/v1/exceptions}.
     */
    @PostMapping("/run")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public BatchReconciliationResponse run(@Valid @RequestBody BatchReconciliationRequest request) {
        return BatchReconciliationResponse.of(
                reconciliationService.reconcileAll(request.transactionIds()).size());
    }
}
