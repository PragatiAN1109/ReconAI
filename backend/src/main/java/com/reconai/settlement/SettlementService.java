package com.reconai.settlement;

import com.reconai.common.error.NotFoundException;
import com.reconai.common.id.BusinessIdGenerator;
import com.reconai.settlement.dto.CreateSettlementRequest;
import com.reconai.transaction.TransactionService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.util.List;

/**
 * Application logic for settlements.
 *
 * <p>This service performs authoritative data ingestion only. It records what a
 * processor reported and nothing more: it does not compare the settlement against the
 * transaction, does not decide whether the two reconcile, and does not change the
 * transaction's status. Those are reconciliation concerns, and reconciliation runs only
 * when it is explicitly invoked.
 */
@Service
public class SettlementService {

    private final SettlementRepository settlementRepository;
    private final TransactionService transactionService;
    private final BusinessIdGenerator businessIdGenerator;
    private final Clock clock;

    public SettlementService(SettlementRepository settlementRepository,
                             TransactionService transactionService,
                             BusinessIdGenerator businessIdGenerator,
                             Clock clock) {
        this.settlementRepository = settlementRepository;
        this.transactionService = transactionService;
        this.businessIdGenerator = businessIdGenerator;
        this.clock = clock;
    }

    /**
     * Records a settlement reported by a processor.
     *
     * <p>The referenced transaction must already exist. This is checked before the
     * insert so the caller receives a clear 404 rather than a foreign key violation
     * surfacing as a conflict.
     *
     * <p>No uniqueness is enforced on the transaction: a transaction may legitimately
     * accumulate several settlements, and detecting when that is wrong is the
     * reconciliation engine's responsibility, not this method's.
     *
     * @throws NotFoundException if the referenced transaction does not exist
     */
    @Transactional
    public Settlement create(CreateSettlementRequest request) {
        transactionService.getByTransactionId(request.transactionId());

        Settlement settlement = Settlement.create(
                businessIdGenerator.nextSettlementId(),
                request.transactionId(),
                request.processor(),
                request.settledAmount(),
                request.currency(),
                request.status(),
                request.settlementTimestamp(),
                Instant.now(clock));
        return settlementRepository.save(settlement);
    }

    /**
     * Returns every settlement recorded against a transaction, oldest first.
     *
     * <p>An existing transaction with no settlements yields an empty list. Only an
     * unknown transaction is a 404.
     *
     * @throws NotFoundException if the transaction does not exist
     */
    @Transactional(readOnly = true)
    public List<Settlement> findByTransactionId(String transactionId) {
        transactionService.getByTransactionId(transactionId);
        return settlementRepository
                .findByTransactionIdOrderBySettlementTimestampAscSettlementIdAsc(transactionId);
    }
}
