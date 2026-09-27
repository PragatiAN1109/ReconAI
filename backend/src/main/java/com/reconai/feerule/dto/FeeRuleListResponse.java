package com.reconai.feerule.dto;

import com.reconai.feerule.FeeRule;

import java.util.List;

/**
 * Response body for {@code GET /api/v1/fee-rules}.
 *
 * <p>{@code total} is the number of items returned. There is no pagination, so it is
 * not a count of some larger result set.
 */
public record FeeRuleListResponse(List<FeeRuleResponse> items, int total) {

    public static FeeRuleListResponse of(List<FeeRule> feeRules) {
        List<FeeRuleResponse> items = feeRules.stream().map(FeeRuleResponse::from).toList();
        return new FeeRuleListResponse(items, items.size());
    }
}
