package com.reconai.reconciliation;

import com.reconai.common.money.Money;
import com.reconai.exception.DetectedDiscrepancy;
import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationExceptionService;
import com.reconai.settlement.Settlement;
import com.reconai.settlement.SettlementRepository;
import com.reconai.settlement.SettlementStatus;
import com.reconai.transaction.Transaction;
import com.reconai.transaction.TransactionService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * The deterministic reconciliation engine.
 *
 * <p>This class answers one question: <em>do the authoritative financial records
 * agree?</em> It never answers <em>why they disagree</em>. It has no LLM, no agent, no
 * retrieval, no fee-rule lookup, no policy lookup and no probabilistic logic of any
 * kind. The same transaction and settlements always produce the same result, and the
 * engine works identically whether or not any investigation service exists.
 *
 * <h2>Applicable settlements</h2>
 *
 * Only settlements with status {@link SettlementStatus#COMPLETED} participate. PENDING,
 * FAILED and REVERSED settlements remain stored as authoritative history but are not
 * something the processor has actually settled, so comparing against them would be
 * comparing against money that did not move.
 *
 * <h2>Rule precedence</h2>
 *
 * Exactly one finding is produced per run, taking the first rule that applies:
 *
 * <ol>
 *   <li>{@code MISSING_SETTLEMENT}   — no completed settlement exists</li>
 *   <li>{@code DUPLICATE_SETTLEMENT} — more than one completed settlement exists</li>
 *   <li>{@code CURRENCY_MISMATCH}    — the one settlement is in another currency</li>
 *   <li>{@code AMOUNT_MISMATCH}      — the one settlement is for another amount</li>
 *   <li>SUCCESS                      — the records agree</li>
 * </ol>
 *
 * Evaluation stops at the first match. A settlement in the wrong currency <em>and</em>
 * for the wrong amount is reported only as CURRENCY_MISMATCH: comparing amounts across
 * two currencies is meaningless, so the amount difference would be noise. Likewise
 * duplicates are never resolved by arbitrarily picking one settlement and carrying on,
 * because which one to pick is exactly the question an investigator has to answer.
 *
 * <p>The precedence exists to make results unambiguous. One transaction produces one
 * finding, and the same records always produce the same finding.
 */
@Service
public class ReconciliationService {

    /** Expected/observed tokens for discrepancies that are not about a number. */
    private static final String SETTLEMENT_PRESENT = "SETTLEMENT_PRESENT";
    private static final String NO_SETTLEMENT = "NO_SETTLEMENT";
    private static final String ONE_COMPLETED_SETTLEMENT = "1_COMPLETED_SETTLEMENT";

    private final TransactionService transactionService;
    private final SettlementRepository settlementRepository;
    private final ReconciliationExceptionService exceptionService;
    private final Clock clock;

    public ReconciliationService(TransactionService transactionService,
                                 SettlementRepository settlementRepository,
                                 ReconciliationExceptionService exceptionService,
                                 Clock clock) {
        this.transactionService = transactionService;
        this.settlementRepository = settlementRepository;
        this.exceptionService = exceptionService;
        this.clock = clock;
    }

    /**
     * Reconciles one transaction against its settlements.
     *
     * <p>Neither the transaction nor any settlement is modified. Reconciliation is a
     * comparison: its only side effect is recording a discrepancy when it finds one, and
     * even that is idempotent.
     *
     * @throws com.reconai.common.error.NotFoundException if the transaction does not exist
     */
    @Transactional
    public ReconciliationResult reconcile(String transactionId) {
        Transaction transaction = transactionService.getByTransactionId(transactionId);
        List<Settlement> completedSettlements = completedSettlementsFor(transactionId);
        Instant reconciledAt = Instant.now(clock);

        return evaluate(transaction, completedSettlements)
                // recordOrReuse returns the existing unresolved exception when this
                // discrepancy is already known, so re-running changes nothing.
                .map(discrepancy -> ReconciliationResult.withException(
                        transactionId, exceptionService.recordOrReuse(discrepancy), reconciledAt))
                .orElseGet(() -> ReconciliationResult.reconciled(transactionId, reconciledAt));
    }

    /**
     * Reconciles several transactions in one synchronous pass.
     *
     * <p>The whole batch shares a transaction, so an unknown ID fails the request and
     * leaves nothing behind rather than half-applying it. There is no queue, no worker
     * and no scheduler: the work is done by the time the call returns.
     */
    @Transactional
    public List<ReconciliationResult> reconcileAll(List<String> transactionIds) {
        return transactionIds.stream().map(this::reconcile).toList();
    }

    /**
     * Returns the settlements that participate in reconciliation, oldest first.
     *
     * <p>Non-COMPLETED settlements are filtered out here and nowhere else, so there is a
     * single place that defines what "applicable" means. Nothing is deleted or altered.
     */
    private List<Settlement> completedSettlementsFor(String transactionId) {
        return settlementRepository
                .findByTransactionIdOrderBySettlementTimestampAscSettlementIdAsc(transactionId)
                .stream()
                .filter(settlement -> settlement.getStatus() == SettlementStatus.COMPLETED)
                .toList();
    }

    /**
     * Applies the deterministic rules in precedence order and returns the first finding.
     *
     * <p>Pure: it reads no database and writes nothing. Given the same inputs it always
     * returns the same output, which is what makes the engine testable in isolation and
     * safe to reason about.
     *
     * @return the discrepancy found, or empty when the records agree
     */
    private Optional<DetectedDiscrepancy> evaluate(Transaction transaction,
                                                   List<Settlement> completedSettlements) {

        // Rule 1: nothing was settled.
        if (completedSettlements.isEmpty()) {
            return Optional.of(missingSettlement(transaction));
        }

        // Rule 2: more was settled than should have been. Stop here rather than picking
        // one settlement to compare, which would hide the duplication.
        if (completedSettlements.size() > 1) {
            return Optional.of(duplicateSettlement(transaction, completedSettlements));
        }

        Settlement settlement = completedSettlements.get(0);

        // Rule 3: settled in the wrong currency. Amounts are not comparable across
        // currencies, so evaluation stops here even if the amount also differs.
        if (!transaction.getCurrency().equalsIgnoreCase(settlement.getCurrency())) {
            return Optional.of(currencyMismatch(transaction, settlement));
        }

        // Rule 4: settled for the wrong amount.
        //
        // The comparison is against expectedSettlementAmount, never against
        // transaction.amount. They differ whenever a deduction is already anticipated,
        // and using the gross amount would raise a discrepancy for every such
        // transaction. compareTo is used rather than equals so that 100.00 and 100.0000
        // are recognised as the same amount.
        if (transaction.getExpectedSettlementAmount()
                .compareTo(settlement.getSettledAmount()) != 0) {
            return Optional.of(amountMismatch(transaction, settlement));
        }

        // Rule 5: the records agree. Nothing is recorded for a transaction that
        // reconciles cleanly.
        return Optional.empty();
    }

    private DetectedDiscrepancy missingSettlement(Transaction transaction) {
        return new DetectedDiscrepancy(
                transaction.getTransactionId(),
                ExceptionType.MISSING_SETTLEMENT,
                null,
                SETTLEMENT_PRESENT,
                NO_SETTLEMENT,
                null,
                transaction.getCurrency());
    }

    private DetectedDiscrepancy duplicateSettlement(Transaction transaction,
                                                    List<Settlement> completedSettlements) {
        // Sorted before joining so the observed value, and therefore the idempotency
        // natural key, does not depend on insertion or query order.
        String settlementIds = completedSettlements.stream()
                .map(Settlement::getSettlementId)
                .sorted(Comparator.naturalOrder())
                .collect(Collectors.joining(","));

        return new DetectedDiscrepancy(
                transaction.getTransactionId(),
                ExceptionType.DUPLICATE_SETTLEMENT,
                null,
                ONE_COMPLETED_SETTLEMENT,
                settlementIds,
                null,
                transaction.getCurrency());
    }

    private DetectedDiscrepancy currencyMismatch(Transaction transaction, Settlement settlement) {
        return new DetectedDiscrepancy(
                transaction.getTransactionId(),
                ExceptionType.CURRENCY_MISMATCH,
                settlement.getSettlementId(),
                transaction.getCurrency(),
                settlement.getCurrency(),
                null,
                transaction.getCurrency());
    }

    private DetectedDiscrepancy amountMismatch(Transaction transaction, Settlement settlement) {
        BigDecimal expected = transaction.getExpectedSettlementAmount();
        BigDecimal observed = settlement.getSettledAmount();

        // Defined as expected minus observed, and deliberately signed. A positive
        // difference means less arrived than expected; a negative one means more did.
        // Taking the absolute value would discard which of those happened.
        BigDecimal difference = expected.subtract(observed);

        return new DetectedDiscrepancy(
                transaction.getTransactionId(),
                ExceptionType.AMOUNT_MISMATCH,
                settlement.getSettlementId(),
                amountToken(expected),
                amountToken(observed),
                difference,
                transaction.getCurrency());
    }

    /**
     * Renders a monetary value for the expected/observed columns.
     *
     * <p>Both columns are part of the idempotency natural key, so this has to be a
     * stable function of the stored amount. Stored amounts always have the column's
     * scale, and this normalisation is deterministic, so re-reconciling unchanged
     * records reproduces the same key.
     */
    private String amountToken(BigDecimal amount) {
        return Money.forResponse(amount).toPlainString();
    }
}
