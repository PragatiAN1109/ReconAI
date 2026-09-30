package com.reconai.demo.dto;

import com.reconai.reconciliation.dto.ReconciliationResponse;

import java.util.List;

/**
 * What a demo run created, plus the reconciliation verdict.
 *
 * <p>{@code reconciliation} is the financial core's own
 * {@link ReconciliationResponse}, unmodified. The demo endpoint reports exactly what the
 * deterministic engine reported — the same record the non-demo reconciliation route
 * returns — so nothing about the result is specific to this endpoint.
 *
 * <p>The created identifiers are echoed because the caller did not choose them and
 * otherwise could not follow the records it just created.
 */
public record DemoReconcileResponse(
        String transactionId,
        List<String> settlementIds,
        ReconciliationResponse reconciliation) {
}
