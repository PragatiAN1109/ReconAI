package com.reconai.settlement;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

/** Persistence access for settlements. */
public interface SettlementRepository extends JpaRepository<Settlement, UUID> {

    /**
     * Returns every settlement for a transaction, oldest first.
     *
     * <p>Ordering is explicit rather than left to the database so that responses and,
     * in a later stage, reconciliation see the same sequence on every run. The
     * settlement identifier breaks ties between settlements sharing a timestamp, which
     * is exactly the case duplicate detection has to handle.
     */
    List<Settlement> findByTransactionIdOrderBySettlementTimestampAscSettlementIdAsc(
            String transactionId);
}
