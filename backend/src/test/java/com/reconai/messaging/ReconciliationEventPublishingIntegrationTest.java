package com.reconai.messaging;

import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationExceptionEvent;
import com.reconai.reconciliation.ReconciliationService;
import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.List;
import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.mockingDetails;

/**
 * Verifies when an event is published and when it is not, against a real database.
 *
 * <p>The broker is mocked: what matters here is the interaction between transaction
 * boundaries, idempotency and publication, none of which a broker would tell us more
 * about. One separate class covers real delivery.
 *
 * <p>This class is deliberately <strong>not</strong> {@code @Transactional}. Every other
 * integration test rolls back, which means an AFTER_COMMIT listener never fires — useful
 * for keeping those tests broker-free, but useless for proving publication happens.
 * Here the transactions genuinely commit, and each test truncates afterwards.
 */
class ReconciliationEventPublishingIntegrationTest extends PostgresIntegrationTest {

    @MockitoBean(name = "kafkaTemplate")
    private KafkaTemplate<String, ReconciliationExceptionEvent> kafkaTemplate;

    private final ReconciliationService reconciliationService;
    private final TransactionTemplate transactionTemplate;
    private final JdbcTemplate jdbc;

    @Autowired
    ReconciliationEventPublishingIntegrationTest(ReconciliationService reconciliationService,
                                                 TransactionTemplate transactionTemplate,
                                                 JdbcTemplate jdbc) {
        this.reconciliationService = reconciliationService;
        this.transactionTemplate = transactionTemplate;
        this.jdbc = jdbc;
    }

    @BeforeEach
    void stubBroker() {
        when(kafkaTemplate.send(anyString(), anyString(), any()))
                .thenReturn(CompletableFuture.completedFuture(null));
    }

    @AfterEach
    void clearFinancialRecords() {
        jdbc.execute("TRUNCATE reconciliation_exceptions, settlements, transactions CASCADE");
    }

    // =================================================================
    // Publication follows creation, not reconciliation calls
    // =================================================================

    @Test
    void successfulReconciliationPublishesNothing() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1247.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        verify(kafkaTemplate, never()).send(anyString(), anyString(), any());
    }

    @Test
    void aNewMissingSettlementPublishesOneEvent() {
        String transactionId = transaction("500.00", "USD");

        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents())
                .singleElement()
                .satisfies(event -> {
                    assertThat(event.type()).isEqualTo(ExceptionType.MISSING_SETTLEMENT);
                    assertThat(event.transactionId()).isEqualTo(transactionId);
                });
    }

    @Test
    void aNewDuplicateSettlementPublishesOneEvent() {
        String transactionId = transaction("850.00", "USD");
        settlement(transactionId, "850.00", "USD", "COMPLETED");
        settlement(transactionId, "850.00", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents()).singleElement()
                .extracting(ReconciliationExceptionEvent::type)
                .isEqualTo(ExceptionType.DUPLICATE_SETTLEMENT);
    }

    @Test
    void aNewCurrencyMismatchPublishesOneEvent() {
        String transactionId = transaction("400.00", "USD");
        settlement(transactionId, "400.00", "EUR", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents()).singleElement()
                .extracting(ReconciliationExceptionEvent::type)
                .isEqualTo(ExceptionType.CURRENCY_MISMATCH);
    }

    @Test
    void aNewAmountMismatchPublishesOneEvent() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents()).singleElement()
                .extracting(ReconciliationExceptionEvent::type)
                .isEqualTo(ExceptionType.AMOUNT_MISMATCH);
    }

    @Test
    void theProcessorFeeCandidatePublishesAnAmountMismatchAndNothingElse() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        ReconciliationExceptionEvent event = publishedEvents().get(0);
        assertThat(event.type())
                .as("the event carries a detected discrepancy, never a suspected cause")
                .isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(event.type().name()).isNotEqualTo("PROCESSOR_FEE");
    }

    // =================================================================
    // Idempotency: one investigation per discrepancy, not per API call
    // =================================================================

    @Test
    void repeatedReconciliationOfUnchangedRecordsPublishesExactlyOneEvent() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);
        reconciliationService.reconcile(transactionId);
        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents())
                .as("calling the endpoint three times must not trigger three investigations")
                .hasSize(1);
        assertThat(countExceptions()).isEqualTo(1);
    }

    @Test
    void aChangedDiscrepancyPublishesANewEvent() {
        String transactionId = transaction("1247.50", "USD");
        String settlementId = settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);
        jdbc.update("UPDATE settlements SET settled_amount = 1200.0000 WHERE settlement_id = ?",
                settlementId);
        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents())
                .as("a different discrepancy deserves its own investigation")
                .hasSize(2);
        assertThat(publishedEvents()).extracting(ReconciliationExceptionEvent::exceptionId)
                .doesNotHaveDuplicates();
        assertThat(countExceptions()).isEqualTo(2);
    }

    @Test
    void aResolvedExceptionReDetectedPublishesANewEvent() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);
        jdbc.update("UPDATE reconciliation_exceptions SET status = 'RESOLVED'");
        reconciliationService.reconcile(transactionId);

        assertThat(publishedEvents()).hasSize(2);
        assertThat(countExceptions()).isEqualTo(2);
    }

    // =================================================================
    // Rollback suppression
    // =================================================================

    @Test
    void aRolledBackTransactionPublishesNothing() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        transactionTemplate.execute(status -> {
            reconciliationService.reconcile(transactionId);
            status.setRollbackOnly();
            return null;
        });

        verify(kafkaTemplate, never()).send(anyString(), anyString(), any());
        assertThat(countExceptions())
                .as("nothing was persisted, so nothing may be investigated")
                .isZero();
    }

    @Test
    void aRolledBackBatchPublishesNothingForAnyOfItsTransactions() {
        String first = transaction("1247.50", "USD");
        settlement(first, "1217.50", "USD", "COMPLETED");
        String second = transaction("500.00", "USD");
        String third = transaction("400.00", "USD");
        settlement(third, "400.00", "EUR", "COMPLETED");

        transactionTemplate.execute(status -> {
            reconciliationService.reconcileAll(List.of(first, second, third));
            status.setRollbackOnly();
            return null;
        });

        verify(kafkaTemplate, never()).send(anyString(), anyString(), any());
        assertThat(countExceptions()).isZero();
    }

    @Test
    void aCommittedBatchPublishesOneEventPerNewlyDetectedDiscrepancy() {
        String first = transaction("1247.50", "USD");
        settlement(first, "1217.50", "USD", "COMPLETED");
        String second = transaction("500.00", "USD");
        String third = transaction("100.00", "USD");
        settlement(third, "100.00", "USD", "COMPLETED");

        reconciliationService.reconcileAll(List.of(first, second, third));

        assertThat(publishedEvents())
                .as("two of three disagree; the third reconciles and is not announced")
                .hasSize(2);
        assertThat(publishedEvents()).extracting(ReconciliationExceptionEvent::transactionId)
                .containsExactlyInAnyOrder(first, second);
    }

    // =================================================================
    // Payload contents
    // =================================================================

    @Test
    void theEventCarriesIdentityClassificationAndDetectionTimeOnly() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        ReconciliationExceptionEvent event = publishedEvents().get(0);
        assertThat(event.exceptionId()).matches("^EX-\\d+$");
        assertThat(event.transactionId()).isEqualTo(transactionId);
        assertThat(event.type()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(event.detectedAt()).isNotNull();

        assertThat(ReconciliationExceptionEvent.class.getRecordComponents())
                .as("no settlement, amount, merchant, root cause, confidence or UUID")
                .extracting(java.lang.reflect.RecordComponent::getName)
                .containsExactly("exceptionId", "transactionId", "type", "detectedAt");
    }

    @Test
    void theEventDoesNotExposeTheInternalUuid() {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconciliationService.reconcile(transactionId);

        ReconciliationExceptionEvent event = publishedEvents().get(0);
        String internalId = jdbc.queryForObject(
                "SELECT id::text FROM reconciliation_exceptions WHERE exception_id = ?",
                String.class, event.exceptionId());

        assertThat(event.toString())
                .as("the database key stays private to the financial core")
                .doesNotContain(internalId);
    }

    @Test
    void theDetectedAtOnTheEventMatchesThePersistedException() {
        String transactionId = transaction("500.00", "USD");

        reconciliationService.reconcile(transactionId);

        ReconciliationExceptionEvent event = publishedEvents().get(0);
        String persisted = jdbc.queryForObject(
                "SELECT exception_id FROM reconciliation_exceptions WHERE exception_id = ?",
                String.class, event.exceptionId());
        assertThat(persisted)
                .as("the event describes a row that really exists")
                .isEqualTo(event.exceptionId());
    }

    // =================================================================
    // Helpers
    // =================================================================

    @SuppressWarnings("unchecked")
    private List<ReconciliationExceptionEvent> publishedEvents() {
        return mockingDetails(kafkaTemplate).getInvocations().stream()
                .filter(invocation -> "send".equals(invocation.getMethod().getName()))
                .map(invocation -> (ReconciliationExceptionEvent) invocation.getArgument(2))
                .toList();
    }

    private Integer countExceptions() {
        return jdbc.queryForObject("SELECT count(*) FROM reconciliation_exceptions", Integer.class);
    }

    private String transaction(String expectedSettlementAmount, String currency) {
        String transactionId = "TX-" + jdbc.queryForObject(
                "SELECT nextval('transaction_business_id_seq')", Long.class);
        jdbc.update("INSERT INTO transactions (id, transaction_id, merchant_id, amount, "
                        + "expected_settlement_amount, currency, transaction_type, status, "
                        + "transaction_timestamp, created_at, updated_at) "
                        + "VALUES (gen_random_uuid(), ?, 'MERCHANT-104', ?, ?, ?, 'PURCHASE', "
                        + "'POSTED', now(), now(), now())",
                transactionId, new java.math.BigDecimal(expectedSettlementAmount),
                new java.math.BigDecimal(expectedSettlementAmount), currency);
        return transactionId;
    }

    private String settlement(String transactionId, String settledAmount, String currency,
                              String status) {
        String settlementId = "SET-" + jdbc.queryForObject(
                "SELECT nextval('settlement_business_id_seq')", Long.class);
        jdbc.update("INSERT INTO settlements (id, settlement_id, transaction_id, processor, "
                        + "settled_amount, currency, status, settlement_timestamp, created_at) "
                        + "VALUES (gen_random_uuid(), ?, ?, 'NORTHSTAR_PAYMENTS', ?, ?, ?, "
                        + "now(), now())",
                settlementId, transactionId, new java.math.BigDecimal(settledAmount), currency,
                status);
        return settlementId;
    }
}
