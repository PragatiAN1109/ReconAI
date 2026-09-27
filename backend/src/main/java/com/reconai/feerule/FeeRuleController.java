package com.reconai.feerule;

import com.reconai.feerule.dto.FeeRuleListResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Fee rule endpoint.
 *
 * <p>Read-only, deliberately. Fee rules are reference data; there is no endpoint through
 * which a caller could add or change one, which matters because the investigation
 * service reads this to explain money that has already moved.
 */
@RestController
@RequestMapping("/api/v1/fee-rules")
public class FeeRuleController {

    private final FeeRuleService feeRuleService;

    public FeeRuleController(FeeRuleService feeRuleService) {
        this.feeRuleService = feeRuleService;
    }

    /**
     * Lists fee rules, optionally filtered. Omitted parameters are not applied, so the
     * filters combine freely.
     *
     * <p>A merchant filter also returns rules that name no merchant, since those apply
     * to every merchant on the processor.
     */
    @GetMapping
    public FeeRuleListResponse list(
            @RequestParam(required = false) String merchantId,
            @RequestParam(required = false) String processor,
            @RequestParam(required = false) String currency,
            @RequestParam(required = false) Boolean active) {
        return FeeRuleListResponse.of(
                feeRuleService.search(merchantId, processor, currency, active));
    }
}
