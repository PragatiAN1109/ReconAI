package com.reconai.transaction;

import com.reconai.common.error.NotFoundException;
import com.reconai.common.id.BusinessIdGenerator;
import com.reconai.transaction.dto.CreateTransactionRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;

/** Application logic for transactions. */
@Service
public class TransactionService {

    private final TransactionRepository transactionRepository;
    private final BusinessIdGenerator businessIdGenerator;
    private final Clock clock;

    public TransactionService(TransactionRepository transactionRepository,
                              BusinessIdGenerator businessIdGenerator,
                              Clock clock) {
        this.transactionRepository = transactionRepository;
        this.businessIdGenerator = businessIdGenerator;
        this.clock = clock;
    }

    /**
     * Records a new transaction and allocates its business identifier.
     *
     * <p>Identifier allocation and insertion share one transaction, so a failed write
     * cannot leave a partially recorded financial record.
     */
    @Transactional
    public Transaction create(CreateTransactionRequest request) {
        Instant now = Instant.now(clock);
        Transaction transaction = Transaction.create(
                businessIdGenerator.nextTransactionId(),
                request.merchantId(),
                request.amount(),
                request.expectedSettlementAmount(),
                request.currency(),
                request.transactionType(),
                request.status(),
                request.transactionTimestamp(),
                now);
        return transactionRepository.save(transaction);
    }

    /**
     * Returns the transaction with the given business identifier.
     *
     * @throws NotFoundException if no such transaction exists
     */
    @Transactional(readOnly = true)
    public Transaction getByTransactionId(String transactionId) {
        return transactionRepository.findByTransactionId(transactionId)
                .orElseThrow(() -> new NotFoundException(
                        "Transaction " + transactionId + " was not found."));
    }
}
