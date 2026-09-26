package com.reconai.reconciliation;

import com.reconai.exception.DetectedDiscrepancy;
import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationException;
import com.reconai.exception.ReconciliationExceptionService;
import com.reconai.settlement.Settlement;
import com.reconai.settlement.SettlementRepository;
import com.reconai.settlement.SettlementStatus;
import com.reconai.transaction.Transaction;
import com.reconai.transaction.TransactionService;
import com.reconai.transaction.TransactionStatus;
import com.reconai.transaction.TransactionType;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Unit tests for the deterministic rule engine.
 *
 * <p>Each test asserts the {@link DetectedDiscrepancy} the engine hands to persistence,
 * which is precisely the decision the engine is responsible for. Persistence itself is
 * mocked, so these tests exercise rule evaluation and nothing else.
 *
 * <p>The rules are applied in this order, and evaluation stops at the first match:
 *
 * <ol>
 *   <li>MISSING_SETTLEMENT</li>
 *   <li>DUPLICATE_SETTLEMENT</li>
 *   <li>CURRENCY_MISMATCH</li>
 *   <li>AMOUNT_MISMATCH</li>
 *   <li>SUCCESS</li>
 * </ol>
 */
@ExtendWith(MockitoExtension.class)
class ReconciliationServiceTest {

    private static final String TRANSACTION_ID = "TX-48291";
    private static final Instant NOW = Instant.parse("2026-09-26T14:32:00Z");

    @Mock
    private TransactionService transactionService;
    @Mock
    private SettlementRepository settlementRepository;
    @Mock
    private ReconciliationExceptionService exceptionService;

    private ReconciliationService reconciliationService;
    private AtomicInteger exceptionCounter;

    @BeforeEach
    void setUp() {
        exceptionCounter = new AtomicInteger(1041);
        reconciliationService = new ReconciliationService(transactionService, settlementRepository,
                exceptionService, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    // =================================================================
    // Rule 1 - MISSING_SETTLEMENT
    // =================================================================

    @Test
    void noSettlementsAtAllIsReportedAsMissingSettlement() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.MISSING_SETTLEMENT);
        assertThat(discrepancy.settlementId()).isNull();
        assertThat(discrepancy.expectedValue()).isEqualTo("SETTLEMENT_PRESENT");
        assertThat(discrepancy.observedValue()).isEqualTo("NO_SETTLEMENT");
        assertThat(discrepancy.differenceAmount()).isNull();
        assertThat(discrepancy.currency()).isEqualTo("USD");
    }

    @Test
    void onlyAPendingSettlementIsReportedAsMissingSettlement() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8001", "1247.50", "USD", SettlementStatus.PENDING));

        assertThat(discrepancy.exceptionType())
                .as("a settlement that has not completed has not moved money")
                .isEqualTo(ExceptionType.MISSING_SETTLEMENT);
    }

    @Test
    void onlyAFailedSettlementIsReportedAsMissingSettlement() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8001", "1247.50", "USD", SettlementStatus.FAILED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.MISSING_SETTLEMENT);
    }

    @Test
    void onlyAReversedSettlementIsReportedAsMissingSettlement() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8001", "1247.50", "USD", SettlementStatus.REVERSED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.MISSING_SETTLEMENT);
    }

    @Test
    void severalNonCompletedSettlementsAreStillReportedAsMissingSettlement() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8001", "1247.50", "USD", SettlementStatus.PENDING),
                settlement("SET-8002", "1247.50", "USD", SettlementStatus.FAILED),
                settlement("SET-8003", "1247.50", "USD", SettlementStatus.REVERSED));

        assertThat(discrepancy.exceptionType())
                .as("three settlements exist but none of them completed")
                .isEqualTo(ExceptionType.MISSING_SETTLEMENT);
    }

    // =================================================================
    // Non-completed settlements are ignored, not deleted
    // =================================================================

    @Test
    void oneCompletedAlongsideAFailedSettlementIsEvaluatedAsASingleSettlement() {
        ReconciliationResult result = reconcile(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8001", "1247.50", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8002", "999.99", "USD", SettlementStatus.FAILED));

        assertThat(result.reconciled())
                .as("the failed settlement neither counts as a duplicate nor as a mismatch")
                .isTrue();
        verify(exceptionService, never()).recordOrReuse(any());
    }

    @Test
    void oneCompletedAlongsideAPendingSettlementIsEvaluatedAsASingleSettlement() {
        ReconciliationResult result = reconcile(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8001", "1247.50", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8002", "1247.50", "USD", SettlementStatus.PENDING));

        assertThat(result.reconciled()).isTrue();
    }

    // =================================================================
    // Rule 2 - DUPLICATE_SETTLEMENT
    // =================================================================

    @Test
    void twoCompletedSettlementsAreReportedAsDuplicateSettlement() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("850.00", "850.00", "USD"),
                settlement("SET-10031", "850.00", "USD", SettlementStatus.COMPLETED),
                settlement("SET-10094", "850.00", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.DUPLICATE_SETTLEMENT);
        assertThat(discrepancy.settlementId())
                .as("a duplicate concerns a set of settlements, not one of them")
                .isNull();
        assertThat(discrepancy.expectedValue()).isEqualTo("1_COMPLETED_SETTLEMENT");
        assertThat(discrepancy.observedValue()).isEqualTo("SET-10031,SET-10094");
        assertThat(discrepancy.differenceAmount()).isNull();
        assertThat(discrepancy.currency()).isEqualTo("USD");
    }

    @Test
    void twoCompletedSettlementsAlongsideFailedAndPendingOnesAreStillADuplicate() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("850.00", "850.00", "USD"),
                settlement("SET-8001", "850.00", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8002", "850.00", "USD", SettlementStatus.FAILED),
                settlement("SET-8003", "850.00", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8004", "850.00", "USD", SettlementStatus.PENDING));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.DUPLICATE_SETTLEMENT);
        assertThat(discrepancy.observedValue())
                .as("only the completed settlements are named")
                .isEqualTo("SET-8001,SET-8003");
    }

    @Test
    void duplicateSettlementIdsAreSortedRegardlessOfTheOrderTheyArrivedIn() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("850.00", "850.00", "USD"),
                settlement("SET-8005", "850.00", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8004", "850.00", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.observedValue())
                .as("the natural key must not depend on insertion or query order")
                .isEqualTo("SET-8004,SET-8005");
    }

    @Test
    void duplicateSettlementTakesPrecedenceOverCurrencyAndAmountDifferences() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("850.00", "850.00", "USD"),
                settlement("SET-8001", "111.11", "EUR", SettlementStatus.COMPLETED),
                settlement("SET-8002", "222.22", "GBP", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType())
                .as("picking one of the duplicates to compare would hide the duplication")
                .isEqualTo(ExceptionType.DUPLICATE_SETTLEMENT);
    }

    @Test
    void threeCompletedSettlementsAreAllNamed() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("850.00", "850.00", "USD"),
                settlement("SET-8003", "850.00", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8001", "850.00", "USD", SettlementStatus.COMPLETED),
                settlement("SET-8002", "850.00", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.observedValue()).isEqualTo("SET-8001,SET-8002,SET-8003");
    }

    // =================================================================
    // Rule 3 - CURRENCY_MISMATCH
    // =================================================================

    @Test
    void settlementInAnotherCurrencyIsReportedAsCurrencyMismatch() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1247.50", "EUR", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.CURRENCY_MISMATCH);
        assertThat(discrepancy.settlementId()).isEqualTo("SET-8821");
        assertThat(discrepancy.expectedValue()).isEqualTo("USD");
        assertThat(discrepancy.observedValue()).isEqualTo("EUR");
        assertThat(discrepancy.differenceAmount())
                .as("amounts in different currencies have no meaningful difference")
                .isNull();
        assertThat(discrepancy.currency()).isEqualTo("USD");
    }

    @Test
    void currencyMismatchTakesPrecedenceOverAnAmountDifference() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1217.50", "EUR", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType())
                .as("comparing 1247.50 USD against 1217.50 EUR would be meaningless")
                .isEqualTo(ExceptionType.CURRENCY_MISMATCH);
        assertThat(discrepancy.differenceAmount()).isNull();
    }

    // =================================================================
    // Rule 4 - AMOUNT_MISMATCH
    // =================================================================

    @Test
    void settlementForAnotherAmountIsReportedAsAmountMismatch() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1217.50", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(discrepancy.settlementId()).isEqualTo("SET-8821");
        assertThat(discrepancy.expectedValue()).isEqualTo("1247.50");
        assertThat(discrepancy.observedValue()).isEqualTo("1217.50");
        assertThat(discrepancy.differenceAmount()).isEqualByComparingTo("30.00");
        assertThat(discrepancy.currency()).isEqualTo("USD");
    }

    /**
     * The scenario the whole architecture is built around. A $30 gap is a fact the
     * engine can establish. That a processor fee caused it is a conclusion the engine
     * has no evidence for and must not reach.
     */
    @Test
    void theProcessorFeeCandidateIsClassifiedOnlyAsAnAmountMismatch() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1217.50", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(discrepancy.differenceAmount()).isEqualByComparingTo("30.00");

        assertThat(ExceptionType.values())
                .extracting(Enum::name)
                .as("the deterministic core knows of no fee rules, merchants or policies")
                .doesNotContain("PROCESSOR_FEE");
        assertThat(discrepancy.exceptionType().name()).isNotEqualTo("PROCESSOR_FEE");
    }

    @Test
    void differenceIsNegativeWhenMoreWasSettledThanExpected() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("100.00", "100.00", "USD"),
                settlement("SET-8821", "120.00", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.differenceAmount())
                .as("the sign says which direction the discrepancy runs; abs() would lose it")
                .isEqualByComparingTo("-20.00");
    }

    @Test
    void differencePreservesPrecisionToFourDecimalPlaces() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1247.5678", "1247.5678", "USD"),
                settlement("SET-8821", "1217.1234", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.differenceAmount()).isEqualByComparingTo("30.4444");
    }

    @Test
    void aSettlementOfZeroAgainstANonZeroExpectationIsAnAmountMismatch() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("500.00", "500.00", "USD"),
                settlement("SET-8821", "0.00", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(discrepancy.differenceAmount()).isEqualByComparingTo("500.00");
    }

    // =================================================================
    // expectedSettlementAmount, never transaction.amount
    // =================================================================

    @Test
    void reconciliationComparesExpectedSettlementAmountRatherThanTransactionAmount() {
        // A deduction is already anticipated: the gross amount is 1000.00 but only
        // 970.00 is expected to settle, and exactly 970.00 arrived.
        ReconciliationResult result = reconcile(
                transaction("1000.00", "970.00", "USD"),
                settlement("SET-8821", "970.00", "USD", SettlementStatus.COMPLETED));

        assertThat(result.reconciled())
                .as("comparing against transaction.amount would flag every anticipated deduction")
                .isTrue();
        verify(exceptionService, never()).recordOrReuse(any());
    }

    @Test
    void aSettlementMatchingTheGrossAmountInsteadOfTheExpectedOneIsAMismatch() {
        DetectedDiscrepancy discrepancy = reconcileAndCapture(
                transaction("1000.00", "970.00", "USD"),
                settlement("SET-8821", "1000.00", "USD", SettlementStatus.COMPLETED));

        assertThat(discrepancy.exceptionType()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(discrepancy.expectedValue())
                .as("the expectation is 970.00, not the 1000.00 gross amount")
                .isEqualTo("970.00");
        assertThat(discrepancy.differenceAmount()).isEqualByComparingTo("-30.00");
    }

    // =================================================================
    // Rule 5 - SUCCESS
    // =================================================================

    @Test
    void matchingTransactionAndSettlementReconcileWithNoException() {
        ReconciliationResult result = reconcile(
                transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1247.50", "USD", SettlementStatus.COMPLETED));

        assertThat(result.reconciled()).isTrue();
        assertThat(result.exceptions()).isEmpty();
        assertThat(result.transactionId()).isEqualTo(TRANSACTION_ID);
        assertThat(result.reconciledAt()).isEqualTo(NOW);
        verify(exceptionService, never()).recordOrReuse(any());
    }

    @Test
    void scaleDifferencesDoNotMakeAmountsUnequal() {
        ReconciliationResult result = reconcile(
                transaction("100.00", "100.00", "USD"),
                settlement("SET-8821", "100.0000", "USD", SettlementStatus.COMPLETED));

        assertThat(result.reconciled())
                .as("compareTo treats 100.00 and 100.0000 as the same amount; equals would not")
                .isTrue();
        verify(exceptionService, never()).recordOrReuse(any());
    }

    @Test
    void currencyComparisonIsNotDefeatedByCase() {
        ReconciliationResult result = reconcile(
                transaction("100.00", "100.00", "USD"),
                settlement("SET-8821", "100.00", "usd", SettlementStatus.COMPLETED));

        assertThat(result.reconciled())
                .as("records written outside the API must not produce a false mismatch")
                .isTrue();
    }

    @Test
    void aZeroValueTransactionSettledAtZeroReconciles() {
        ReconciliationResult result = reconcile(
                transaction("0.00", "0.00", "USD"),
                settlement("SET-8821", "0.0000", "USD", SettlementStatus.COMPLETED));

        assertThat(result.reconciled()).isTrue();
    }

    // =================================================================
    // Reconciliation observes; it does not mutate
    // =================================================================

    @Test
    void reconciliationDoesNotMutateTheTransactionStatus() {
        Transaction transaction = transaction("1247.50", "1247.50", "USD");
        stubExceptionPersistence();

        reconcile(transaction, settlement("SET-8821", "1217.50", "USD", SettlementStatus.COMPLETED));

        assertThat(transaction.getStatus())
                .as("a discrepancy is recorded against the transaction, not applied to it")
                .isEqualTo(TransactionStatus.POSTED);
    }

    @Test
    void reconciliationDoesNotMutateSettlementStatus() {
        Settlement settlement = settlement("SET-8821", "1217.50", "USD", SettlementStatus.COMPLETED);
        stubExceptionPersistence();

        reconcile(transaction("1247.50", "1247.50", "USD"), settlement);

        assertThat(settlement.getStatus()).isEqualTo(SettlementStatus.COMPLETED);
    }

    @Test
    void successfulReconciliationPersistsNothingAtAll() {
        reconcile(transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1247.50", "USD", SettlementStatus.COMPLETED));

        verify(exceptionService, never()).recordOrReuse(any());
        verify(exceptionService, never()).findUnresolvedEquivalent(any());
    }

    @Test
    void exactlyOneDiscrepancyIsRecordedPerRun() {
        stubExceptionPersistence();

        reconcile(transaction("1247.50", "1247.50", "USD"),
                settlement("SET-8821", "1217.50", "EUR", SettlementStatus.COMPLETED));

        // Wrong currency and wrong amount, but precedence yields a single finding.
        verify(exceptionService).recordOrReuse(any());
    }

    // =================================================================
    // Helpers
    // =================================================================

    private Transaction transaction(String amount, String expectedSettlementAmount,
                                    String currency) {
        return Transaction.create(TRANSACTION_ID, "MERCHANT-104", new BigDecimal(amount),
                new BigDecimal(expectedSettlementAmount), currency, TransactionType.PURCHASE,
                TransactionStatus.POSTED, Instant.parse("2026-09-26T13:45:00Z"), NOW);
    }

    private Settlement settlement(String settlementId, String settledAmount, String currency,
                                  SettlementStatus status) {
        return Settlement.create(settlementId, TRANSACTION_ID, "NORTHSTAR_PAYMENTS",
                new BigDecimal(settledAmount), currency, status,
                Instant.parse("2026-09-26T14:00:00Z"), NOW);
    }

    private ReconciliationResult reconcile(Transaction transaction, Settlement... settlements) {
        when(transactionService.getByTransactionId(TRANSACTION_ID)).thenReturn(transaction);
        when(settlementRepository
                .findByTransactionIdOrderBySettlementTimestampAscSettlementIdAsc(TRANSACTION_ID))
                .thenReturn(Arrays.asList(settlements));
        return reconciliationService.reconcile(TRANSACTION_ID);
    }

    /** Makes the persistence mock return an exception built from whatever it is given. */
    private void stubExceptionPersistence() {
        when(exceptionService.recordOrReuse(any())).thenAnswer(invocation ->
                ReconciliationException.detected("EX-" + exceptionCounter.incrementAndGet(),
                        invocation.getArgument(0), NOW));
    }

    /** Runs reconciliation and returns the discrepancy handed to persistence. */
    private DetectedDiscrepancy reconcileAndCapture(Transaction transaction,
                                                    Settlement... settlements) {
        stubExceptionPersistence();

        ReconciliationResult result = reconcile(transaction, settlements);

        assertThat(result.reconciled())
                .as("a discrepancy was expected")
                .isFalse();
        assertThat(result.exceptions()).hasSize(1);

        ArgumentCaptor<DetectedDiscrepancy> captor =
                ArgumentCaptor.forClass(DetectedDiscrepancy.class);
        verify(exceptionService).recordOrReuse(captor.capture());
        return captor.getValue();
    }
}
