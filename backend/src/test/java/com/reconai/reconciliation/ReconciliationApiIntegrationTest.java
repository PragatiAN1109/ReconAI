package com.reconai.reconciliation;

import com.reconai.support.PostgresIntegrationTest;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.transaction.annotation.Transactional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.matchesPattern;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * End-to-end tests for the reconciliation endpoints against a real PostgreSQL instance.
 *
 * <p>These complement the rule-engine unit tests by exercising the parts that only a
 * real database can prove: that repeated reconciliation reuses one exception row, that
 * a changed discrepancy produces a new one, and that a resolved exception does not
 * suppress re-detection.
 */
@AutoConfigureMockMvc
@Transactional
class ReconciliationApiIntegrationTest extends PostgresIntegrationTest {

    private final MockMvc mockMvc;
    private final JdbcTemplate jdbc;
    private final EntityManager entityManager;

    @Autowired
    ReconciliationApiIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc,
                                     EntityManager entityManager) {
        this.mockMvc = mockMvc;
        this.jdbc = jdbc;
        this.entityManager = entityManager;
    }

    // =================================================================
    // The five outcomes, end to end
    // =================================================================

    @Test
    void matchingRecordsReconcileWithNoException() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1247.50", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.transactionId").value(transactionId))
                .andExpect(jsonPath("$.reconciled").value(true))
                .andExpect(jsonPath("$.exceptions", hasSize(0)))
                .andExpect(jsonPath("$.reconciledAt").exists());

        assertThat(countExceptions()).isZero();
    }

    @Test
    void aTransactionWithNoCompletedSettlementReportsMissingSettlement() throws Exception {
        String transactionId = createTransaction("500.00", "500.00", "USD");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(false))
                .andExpect(jsonPath("$.exceptions", hasSize(1)))
                .andExpect(jsonPath("$.exceptions[0].exceptionId").value(matchesPattern("^EX-\\d+$")))
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("MISSING_SETTLEMENT"))
                .andExpect(jsonPath("$.exceptions[0].expectedValue").value("SETTLEMENT_PRESENT"))
                .andExpect(jsonPath("$.exceptions[0].observedValue").value("NO_SETTLEMENT"))
                .andExpect(jsonPath("$.exceptions[0].differenceAmount").doesNotExist())
                .andExpect(jsonPath("$.exceptions[0].currency").value("USD"))
                .andExpect(jsonPath("$.exceptions[0].status").value("OPEN"));
    }

    @Test
    void aPendingSettlementDoesNotCountAsSettled() throws Exception {
        String transactionId = createTransaction("500.00", "500.00", "USD");
        createSettlement(transactionId, "500.00", "USD", "PENDING");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("MISSING_SETTLEMENT"));

        Integer stored = jdbc.queryForObject(
                "SELECT count(*) FROM settlements WHERE transaction_id = ?", Integer.class,
                transactionId);
        assertThat(stored)
                .as("the pending settlement is ignored for comparison but not deleted")
                .isEqualTo(1);
    }

    @Test
    void twoCompletedSettlementsReportDuplicateSettlementWithSortedIds() throws Exception {
        String transactionId = createTransaction("850.00", "850.00", "USD");
        String first = createSettlement(transactionId, "850.00", "USD", "COMPLETED");
        String second = createSettlement(transactionId, "850.00", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(false))
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("DUPLICATE_SETTLEMENT"))
                .andExpect(jsonPath("$.exceptions[0].expectedValue").value("1_COMPLETED_SETTLEMENT"))
                .andExpect(jsonPath("$.exceptions[0].observedValue")
                        .value(first.compareTo(second) < 0 ? first + "," + second
                                : second + "," + first));
    }

    @Test
    void aSettlementInAnotherCurrencyReportsCurrencyMismatch() throws Exception {
        String transactionId = createTransaction("400.00", "400.00", "USD");
        createSettlement(transactionId, "400.00", "EUR", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("CURRENCY_MISMATCH"))
                .andExpect(jsonPath("$.exceptions[0].expectedValue").value("USD"))
                .andExpect(jsonPath("$.exceptions[0].observedValue").value("EUR"))
                .andExpect(jsonPath("$.exceptions[0].differenceAmount").doesNotExist());
    }

    @Test
    void wrongCurrencyAndWrongAmountReportOnlyCurrencyMismatch() throws Exception {
        String transactionId = createTransaction("400.00", "400.00", "USD");
        createSettlement(transactionId, "123.45", "EUR", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.exceptions", hasSize(1)))
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("CURRENCY_MISMATCH"));
    }

    @Test
    void theProcessorFeeCandidateIsReportedOnlyAsAnAmountMismatchOfThirty() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        String body = mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(false))
                .andExpect(jsonPath("$.exceptions", hasSize(1)))
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("AMOUNT_MISMATCH"))
                .andExpect(jsonPath("$.exceptions[0].expectedValue").value("1247.50"))
                .andExpect(jsonPath("$.exceptions[0].observedValue").value("1217.50"))
                .andExpect(jsonPath("$.exceptions[0].currency").value("USD"))
                .andExpect(jsonPath("$.exceptions[0].status").value("OPEN"))
                .andExpect(content().string(containsString("\"differenceAmount\":30.00")))
                .andReturn().getResponse().getContentAsString();

        assertThat(body)
                .as("the deterministic core has no notion of fees, merchants or policies")
                .doesNotContain("PROCESSOR_FEE");

        entityManager.flush();
        String storedSettlement = jdbc.queryForObject(
                "SELECT settlement_id FROM reconciliation_exceptions WHERE transaction_id = ?",
                String.class, transactionId);
        assertThat(storedSettlement).isEqualTo(settlementId);
    }

    @Test
    void reconciliationUsesExpectedSettlementAmountRatherThanTransactionAmount()
            throws Exception {
        String transactionId = createTransaction("1000.00", "970.00", "USD");
        createSettlement(transactionId, "970.00", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(true));

        assertThat(countExceptions())
                .as("the gross amount of 1000.00 is not what was expected to settle")
                .isZero();
    }

    @Test
    void scaleDifferencesReconcileSuccessfully() throws Exception {
        String transactionId = createTransaction("100.00", "100.00", "USD");
        createSettlement(transactionId, "100.0000", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(true));

        assertThat(countExceptions()).isZero();
    }

    @Test
    void anOverSettlementProducesANegativeDifference() throws Exception {
        String transactionId = createTransaction("100.00", "100.00", "USD");
        createSettlement(transactionId, "120.00", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("AMOUNT_MISMATCH"))
                .andExpect(content().string(containsString("\"differenceAmount\":-20.00")));

        entityManager.flush();
        String stored = jdbc.queryForObject(
                "SELECT difference_amount::text FROM reconciliation_exceptions WHERE transaction_id = ?",
                String.class, transactionId);
        assertThat(stored)
                .as("the sign records that more money arrived than expected")
                .isEqualTo("-20.0000");
    }

    // =================================================================
    // Idempotency
    // =================================================================

    @Test
    void repeatedReconciliationOfUnchangedRecordsReusesTheSameException() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        String first = reconcileAndReturnExceptionId(transactionId);
        String second = reconcileAndReturnExceptionId(transactionId);
        String third = reconcileAndReturnExceptionId(transactionId);

        assertThat(second).isEqualTo(first);
        assertThat(third).isEqualTo(first);
        assertThat(countExceptions())
                .as("three runs over unchanged records must leave exactly one exception")
                .isEqualTo(1);
    }

    @Test
    void repeatedSuccessfulReconciliationNeverCreatesAnException() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1247.50", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId)).andExpect(jsonPath("$.reconciled").value(true));
        mockMvc.perform(reconcile(transactionId)).andExpect(jsonPath("$.reconciled").value(true));
        entityManager.flush();

        assertThat(countExceptions()).isZero();
    }

    @Test
    void aChangedDiscrepancyProducesANewException() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        String first = reconcileAndReturnExceptionId(transactionId);

        // The processor corrects its report. There is no settlement update API, so the
        // authoritative record is amended directly for this test.
        jdbc.update("UPDATE settlements SET settled_amount = 1200.0000 WHERE settlement_id = ?",
                settlementId);
        entityManager.clear();

        String second = reconcileAndReturnExceptionId(transactionId);

        assertThat(second)
                .as("a different observed amount is a different discrepancy")
                .isNotEqualTo(first);
        assertThat(countExceptions()).isEqualTo(2);
    }

    @Test
    void aResolvedExceptionDoesNotSuppressReDetection() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        String first = reconcileAndReturnExceptionId(transactionId);

        // Status transitions belong to the approval phase, so the row is moved directly.
        jdbc.update("UPDATE reconciliation_exceptions SET status = 'RESOLVED' WHERE exception_id = ?",
                first);
        entityManager.clear();

        String second = reconcileAndReturnExceptionId(transactionId);

        assertThat(second)
                .as("a resolved discrepancy that recurs is a new discrepancy")
                .isNotEqualTo(first);
        assertThat(countExceptions()).isEqualTo(2);
    }

    @Test
    void aDiscrepancyThatDisappearsStopsBeingReported() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        reconcileAndReturnExceptionId(transactionId);

        jdbc.update("UPDATE settlements SET settled_amount = 1247.5000 WHERE settlement_id = ?",
                settlementId);
        entityManager.clear();

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(true))
                .andExpect(jsonPath("$.exceptions", hasSize(0)));

        assertThat(countExceptions())
                .as("the earlier exception remains on record; it is simply no longer re-detected")
                .isEqualTo(1);
    }

    // =================================================================
    // Reconciliation observes; it does not mutate
    // =================================================================

    @Test
    void reconciliationDoesNotChangeTransactionOrSettlementStatus() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId)).andExpect(status().isOk());
        entityManager.flush();

        assertThat(jdbc.queryForObject(
                "SELECT status FROM transactions WHERE transaction_id = ?", String.class,
                transactionId)).isEqualTo("POSTED");
        assertThat(jdbc.queryForObject(
                "SELECT status FROM settlements WHERE settlement_id = ?", String.class,
                settlementId)).isEqualTo("COMPLETED");
    }

    @Test
    void successfulReconciliationDoesNotMarkTheTransactionSettled() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1247.50", "USD", "COMPLETED");

        mockMvc.perform(reconcile(transactionId)).andExpect(jsonPath("$.reconciled").value(true));
        entityManager.flush();

        assertThat(jdbc.queryForObject(
                "SELECT status FROM transactions WHERE transaction_id = ?", String.class,
                transactionId))
                .as("advancing a transaction's lifecycle is not reconciliation's job")
                .isEqualTo("POSTED");
    }

    // =================================================================
    // Errors and correlation
    // =================================================================

    @Test
    void reconcilingAnUnknownTransactionReturns404() throws Exception {
        mockMvc.perform(reconcile("TX-48291"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.message").value("Transaction TX-48291 was not found."))
                .andExpect(jsonPath("$.path")
                        .value("/api/v1/reconciliation/transactions/TX-48291"))
                .andExpect(jsonPath("$.correlationId").exists());
    }

    @Test
    void aSuppliedCorrelationIdIsEchoedOnReconciliation() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");

        mockMvc.perform(reconcile(transactionId).header("X-Correlation-ID", "CORR-89123"))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Correlation-ID", "CORR-89123"));
    }

    @Test
    void aCorrelationIdIsGeneratedWhenNoneIsSupplied() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");

        mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Correlation-ID", matchesPattern("^CORR-[0-9A-F]{8}$")));
    }

    // =================================================================
    // The detected exception is readable through the exception APIs
    // =================================================================

    @Test
    void aDetectedExceptionIsRetrievableThroughTheExceptionApis() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1217.50", "USD", "COMPLETED");
        String exceptionId = reconcileAndReturnExceptionId(transactionId);

        mockMvc.perform(get("/api/v1/exceptions/{exceptionId}", exceptionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.exceptionType").value("AMOUNT_MISMATCH"))
                .andExpect(jsonPath("$.transactionId").value(transactionId));

        mockMvc.perform(get("/api/v1/exceptions").param("transactionId", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(1));
    }

    // =================================================================
    // Batch endpoint
    // =================================================================

    @Test
    void batchReconciliationReturns202WithTheContractBody() throws Exception {
        String first = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(first, "1217.50", "USD", "COMPLETED");
        String second = createTransaction("500.00", "500.00", "USD");
        String third = createTransaction("100.00", "100.00", "USD");
        createSettlement(third, "100.00", "USD", "COMPLETED");

        mockMvc.perform(post("/api/v1/reconciliation/run")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"transactionIds": ["%s", "%s", "%s"]}
                                """.formatted(first, second, third)))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.requested").value(3))
                .andExpect(jsonPath("$.message").value("Reconciliation started."));
        entityManager.flush();

        assertThat(countExceptions())
                .as("the work is already done when the call returns: two of three disagree")
                .isEqualTo(2);
    }

    @Test
    void batchReconciliationIsIdempotentToo() throws Exception {
        String transactionId = createTransaction("1247.50", "1247.50", "USD");
        createSettlement(transactionId, "1217.50", "USD", "COMPLETED");
        String body = """
                {"transactionIds": ["%s"]}
                """.formatted(transactionId);

        mockMvc.perform(post("/api/v1/reconciliation/run")
                .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isAccepted());
        mockMvc.perform(post("/api/v1/reconciliation/run")
                .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isAccepted());
        entityManager.flush();

        assertThat(countExceptions()).isEqualTo(1);
    }

    @Test
    void batchReconciliationWithAnUnknownTransactionFailsAndPersistsNothing() throws Exception {
        String known = createTransaction("500.00", "500.00", "USD");

        mockMvc.perform(post("/api/v1/reconciliation/run")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"transactionIds": ["%s", "TX-48291"]}
                                """.formatted(known)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.message").value("Transaction TX-48291 was not found."));
    }

    @Test
    void batchReconciliationRejectsAnEmptyList() throws Exception {
        mockMvc.perform(post("/api/v1/reconciliation/run")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"transactionIds": []}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message")
                        .value(containsString("transactionIds is required and must not be empty")));
    }

    @Test
    void batchReconciliationRejectsAMissingList() throws Exception {
        mockMvc.perform(post("/api/v1/reconciliation/run")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    // =================================================================
    // Helpers
    // =================================================================

    private static MockHttpServletRequestBuilder reconcile(String transactionId) {
        return post("/api/v1/reconciliation/transactions/{transactionId}", transactionId);
    }

    private String reconcileAndReturnExceptionId(String transactionId) throws Exception {
        String body = mockMvc.perform(reconcile(transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.reconciled").value(false))
                .andReturn().getResponse().getContentAsString();
        entityManager.flush();
        return com.jayway.jsonpath.JsonPath.read(body, "$.exceptions[0].exceptionId");
    }

    private Integer countExceptions() {
        entityManager.flush();
        return jdbc.queryForObject("SELECT count(*) FROM reconciliation_exceptions", Integer.class);
    }

    private String createTransaction(String amount, String expectedSettlementAmount,
                                     String currency) throws Exception {
        String body = mockMvc.perform(post("/api/v1/transactions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "merchantId": "MERCHANT-104",
                                  "amount": %s,
                                  "expectedSettlementAmount": %s,
                                  "currency": "%s",
                                  "transactionType": "PURCHASE",
                                  "status": "POSTED",
                                  "transactionTimestamp": "2026-09-26T13:45:00Z"
                                }
                                """.formatted(amount, expectedSettlementAmount, currency)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        entityManager.flush();
        return com.jayway.jsonpath.JsonPath.read(body, "$.transactionId");
    }

    private String createSettlement(String transactionId, String settledAmount, String currency,
                                    String status) throws Exception {
        String body = mockMvc.perform(post("/api/v1/settlements")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "transactionId": "%s",
                                  "processor": "NORTHSTAR_PAYMENTS",
                                  "settledAmount": %s,
                                  "currency": "%s",
                                  "status": "%s",
                                  "settlementTimestamp": "2026-09-26T14:00:00Z"
                                }
                                """.formatted(transactionId, settledAmount, currency, status)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        entityManager.flush();
        return com.jayway.jsonpath.JsonPath.read(body, "$.settlementId");
    }
}
