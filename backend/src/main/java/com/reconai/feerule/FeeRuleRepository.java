package com.reconai.feerule;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.UUID;

/** Read access to fee rules. */
public interface FeeRuleRepository extends JpaRepository<FeeRule, UUID> {

    /**
     * Returns fee rules matching whichever filters were supplied.
     *
     * <p>A null argument means "do not filter on this", which keeps the optional
     * parameters combinable without a criteria builder.
     *
     * <p>A rule with no merchant applies to every merchant on its processor, so a
     * merchant filter deliberately matches those too. Omitting them would hide the
     * processor-wide rules that most often explain a settlement difference.
     *
     * <p>Ordering is by business identifier so two identical requests return the same
     * sequence.
     */
    @Query("""
            SELECT f FROM FeeRule f
            WHERE (:merchantId IS NULL OR f.merchantId = :merchantId OR f.merchantId IS NULL)
              AND (:processor IS NULL OR f.processor = :processor)
              AND (:currency IS NULL OR f.currency = :currency)
              AND (:active IS NULL OR f.active = :active)
            ORDER BY f.ruleId ASC
            """)
    List<FeeRule> search(@Param("merchantId") String merchantId,
                         @Param("processor") String processor,
                         @Param("currency") String currency,
                         @Param("active") Boolean active);
}
