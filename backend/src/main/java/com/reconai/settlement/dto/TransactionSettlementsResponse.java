package com.reconai.settlement.dto;

import com.reconai.settlement.Settlement;

import java.util.List;

/**
 * Response body for {@code GET /api/v1/transactions/{transactionId}/settlements}
 * (docs/api-contract.md section 7.2).
 *
 * <p>The payload is always an array, even when a transaction has exactly one settlement
 * or none at all. A transaction with no settlements yields an empty list rather than a
 * 404 or a null: "this transaction has not been settled" is a real and reconcilable
 * state, not a missing resource.
 */
public record TransactionSettlementsResponse(
        String transactionId,
        List<SettlementResponse> settlements) {

    public static TransactionSettlementsResponse of(String transactionId,
                                                    List<Settlement> settlements) {
        return new TransactionSettlementsResponse(
                transactionId,
                settlements.stream().map(SettlementResponse::from).toList());
    }
}
