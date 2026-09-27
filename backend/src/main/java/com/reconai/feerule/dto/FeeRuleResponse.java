package com.reconai.feerule.dto;

import com.reconai.common.money.Money;
import com.reconai.feerule.FeeRule;
import com.reconai.feerule.FeeType;

import java.math.BigDecimal;

/**
 * One fee rule as returned to a caller.
 *
 * <p>The internal UUID is deliberately absent; callers refer to a rule by its business
 * identifier, which is also what later evidence citations will use.
 */
public record FeeRuleResponse(
        String ruleId,
        String merchantId,
        String processor,
        FeeType feeType,
        BigDecimal feeAmount,
        String currency,
        String description,
        boolean active) {

    public static FeeRuleResponse from(FeeRule feeRule) {
        return new FeeRuleResponse(
                feeRule.getRuleId(),
                feeRule.getMerchantId(),
                feeRule.getProcessor(),
                feeRule.getFeeType(),
                Money.forResponse(feeRule.getFeeAmount()),
                feeRule.getCurrency(),
                feeRule.getDescription(),
                feeRule.isActive());
    }
}
