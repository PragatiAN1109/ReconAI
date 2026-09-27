package com.reconai.feerule;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * Read access to fee configuration.
 *
 * <p>Read-only by construction: there is no create, update or delete path. Fee rules are
 * reference data loaded by migration, not something an API caller sets.
 */
@Service
public class FeeRuleService {

    private final FeeRuleRepository feeRuleRepository;

    public FeeRuleService(FeeRuleRepository feeRuleRepository) {
        this.feeRuleRepository = feeRuleRepository;
    }

    /** Returns fee rules matching the supplied filters. Any filter may be null. */
    @Transactional(readOnly = true)
    public List<FeeRule> search(String merchantId, String processor, String currency,
                                Boolean active) {
        return feeRuleRepository.search(merchantId, processor, currency, active);
    }
}
