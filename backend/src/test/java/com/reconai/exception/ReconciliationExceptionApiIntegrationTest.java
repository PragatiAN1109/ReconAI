package com.reconai.exception;

import com.reconai.support.PostgresIntegrationTest;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Tests the reconciliation exception domain: persistence of each discrepancy type, the
 * read APIs and their filters, and the idempotency support the reconciliation engine
 * will rely on.
 *
 * <p>Exceptions are created here through the service, which is how the engine will
 * create them. Nothing in this stage detects a discrepancy; every discrepancy below is
 * asserted by the test itself.
 */
@AutoConfigureMockMvc
@Transactional
class ReconciliationExceptionApiIntegrationTest extends PostgresIntegrationTest {

    private final MockMvc mockMvc;
    private final JdbcTemplate jdbc;
    private final EntityManager entityManager;
    private final ReconciliationExceptionService exceptionService;

    @Autowired
    ReconciliationExceptionApiIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc,
                                              EntityManager entityManager,
                                              ReconciliationExceptionService exceptionService) {
        this.mockMvc = mockMvc;
        this.jdbc = jdbc;
        this.entityManager = entityManager;
        this.exceptionService = exceptionService;
    }

    // -----------------------------------------------------------------
    // Persistence of each deterministic discrepancy type
    // -----------------------------------------------------------------

    @Test
    void amountMismatchIsPersistedWithItsExpectedObservedAndDifference() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);

        ReconciliationException exception = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.AMOUNT_MISMATCH, settlementId,
                "1247.50", "1217.50", new BigDecimal("30.00"), "USD"));

        assertThat(exception.getExceptionId()).startsWith("EX-");
        assertThat(exception.getExceptionType()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
        assertThat(exception.getSettlementId()).isEqualTo(settlementId);
        assertThat(exception.getExpectedValue()).isEqualTo("1247.50");
        assertThat(exception.getObservedValue()).isEqualTo("1217.50");
        assertThat(exception.getDifferenceAmount()).isEqualByComparingTo("30.00");
        assertThat(exception.getCurrency()).isEqualTo("USD");
        assertThat(exception.getStatus())
                .as("newly detected discrepancies always start OPEN")
                .isEqualTo(ExceptionStatus.OPEN);
    }

    @Test
    void missingSettlementIsPersistedWithANullSettlementId() throws Exception {
        String transactionId = createTransaction();

        ReconciliationException exception = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        entityManager.flush();

        assertThat(exception.getSettlementId()).isNull();
        assertThat(exception.getDifferenceAmount()).isNull();
        assertThat(exception.getCurrency()).isNull();
        assertThat(exception.getExpectedValue()).isEqualTo("SETTLEMENT_PRESENT");
        assertThat(exception.getObservedValue()).isEqualTo("NO_SETTLEMENT");

        String stored = jdbc.queryForObject(
                "SELECT settlement_id FROM reconciliation_exceptions WHERE exception_id = ?",
                String.class, exception.getExceptionId());
        assertThat(stored).isNull();
    }

    @Test
    void duplicateSettlementIsPersistedWithANullSettlementIdAndTheSettlementIdsObserved()
            throws Exception {
        String transactionId = createTransaction();
        String first = createSettlement(transactionId);
        String second = createSettlement(transactionId);

        ReconciliationException exception = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.DUPLICATE_SETTLEMENT, null,
                "1_COMPLETED_SETTLEMENT", first + "," + second, null, null));

        assertThat(exception.getSettlementId())
                .as("a duplicate concerns a set of settlements, not a single one")
                .isNull();
        assertThat(exception.getObservedValue()).isEqualTo(first + "," + second);
    }

    @Test
    void currencyMismatchIsPersistedWithCurrencyCodesAsExpectedAndObservedValues()
            throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);

        ReconciliationException exception = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.CURRENCY_MISMATCH, settlementId,
                "USD", "EUR", null, null));

        assertThat(exception.getExpectedValue()).isEqualTo("USD");
        assertThat(exception.getObservedValue()).isEqualTo("EUR");
        assertThat(exception.getDifferenceAmount())
                .as("a currency mismatch has no meaningful monetary difference")
                .isNull();
    }

    @Test
    void differenceAmountPreservesBigDecimalPrecision() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);

        ReconciliationException exception = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.AMOUNT_MISMATCH, settlementId,
                "1247.5678", "1217.1234", new BigDecimal("30.4444"), "USD"));
        entityManager.flush();

        String stored = jdbc.queryForObject(
                "SELECT difference_amount::text FROM reconciliation_exceptions WHERE exception_id = ?",
                String.class, exception.getExceptionId());

        assertThat(stored).isEqualTo("30.4444");
        assertThat(exception.getDifferenceAmount()).isEqualByComparingTo("30.4444");
    }

    // -----------------------------------------------------------------
    // Idempotency support for the reconciliation engine
    // -----------------------------------------------------------------

    @Test
    void recordingTheSameDiscrepancyTwiceReusesTheExistingUnresolvedException()
            throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        DetectedDiscrepancy discrepancy = amountMismatch(transactionId, settlementId, "1217.50", "30.00");

        ReconciliationException first = exceptionService.recordOrReuse(discrepancy);
        entityManager.flush();
        ReconciliationException second = exceptionService.recordOrReuse(discrepancy);
        entityManager.flush();

        assertThat(second.getExceptionId())
                .as("repeated reconciliation must not accumulate EX-1042, EX-1043, EX-1044 ...")
                .isEqualTo(first.getExceptionId());

        Integer rows = jdbc.queryForObject(
                "SELECT count(*) FROM reconciliation_exceptions WHERE transaction_id = ?",
                Integer.class, transactionId);
        assertThat(rows).isEqualTo(1);
    }

    @Test
    void anUnresolvedEquivalentCanBeFoundWithoutCreatingAnything() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        DetectedDiscrepancy discrepancy = amountMismatch(transactionId, settlementId, "1217.50", "30.00");

        assertThat(exceptionService.findUnresolvedEquivalent(discrepancy)).isEmpty();

        ReconciliationException created = exceptionService.recordOrReuse(discrepancy);
        entityManager.flush();

        assertThat(exceptionService.findUnresolvedEquivalent(discrepancy))
                .containsInstanceOf(ReconciliationException.class)
                .get()
                .extracting(ReconciliationException::getExceptionId)
                .isEqualTo(created.getExceptionId());
    }

    @Test
    void equivalenceIsFoundEvenWhenTheNaturalKeyContainsNulls() throws Exception {
        String transactionId = createTransaction();
        DetectedDiscrepancy missing = new DetectedDiscrepancy(
                transactionId, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null);

        ReconciliationException first = exceptionService.recordOrReuse(missing);
        entityManager.flush();
        ReconciliationException second = exceptionService.recordOrReuse(missing);
        entityManager.flush();

        assertThat(second.getExceptionId())
                .as("a null settlement_id must still compare equal, matching the index COALESCE")
                .isEqualTo(first.getExceptionId());
    }

    @Test
    void aResolvedExceptionDoesNotPreventDetectingTheSameDiscrepancyAgain() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        DetectedDiscrepancy discrepancy = amountMismatch(transactionId, settlementId, "1217.50", "30.00");

        ReconciliationException first = exceptionService.recordOrReuse(discrepancy);
        entityManager.flush();

        // Status transitions are an approval-phase concern, so the test moves the row
        // directly rather than through an API that does not exist yet.
        jdbc.update("UPDATE reconciliation_exceptions SET status = 'RESOLVED' WHERE exception_id = ?",
                first.getExceptionId());
        entityManager.clear();

        ReconciliationException second = exceptionService.recordOrReuse(discrepancy);
        entityManager.flush();

        assertThat(second.getExceptionId())
                .as("a discrepancy that was resolved and recurs is a new discrepancy")
                .isNotEqualTo(first.getExceptionId());
    }

    @Test
    void aDifferentObservedValueProducesADistinctException() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);

        ReconciliationException first = exceptionService.recordOrReuse(
                amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        entityManager.flush();
        ReconciliationException second = exceptionService.recordOrReuse(
                amountMismatch(transactionId, settlementId, "1200.00", "47.50"));
        entityManager.flush();

        assertThat(second.getExceptionId())
                .as("a genuinely changed discrepancy deserves its own record")
                .isNotEqualTo(first.getExceptionId());
    }

    @Test
    void differentExceptionTypesForOneTransactionCoexist() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);

        exceptionService.recordOrReuse(amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.CURRENCY_MISMATCH, settlementId, "USD", "EUR", null, null));
        entityManager.flush();

        Integer rows = jdbc.queryForObject(
                "SELECT count(*) FROM reconciliation_exceptions WHERE transaction_id = ?",
                Integer.class, transactionId);
        assertThat(rows).isEqualTo(2);
    }

    // -----------------------------------------------------------------
    // Defence in depth: PROCESSOR_FEE
    // -----------------------------------------------------------------

    @Test
    void everyExceptionTypeTheEnumDefinesIsAcceptedByTheDatabaseCheckConstraint()
            throws Exception {
        String transactionId = createTransaction();

        for (ExceptionType type : ExceptionType.values()) {
            exceptionService.recordOrReuse(new DetectedDiscrepancy(
                    transactionId, type, null, "expected-" + type, "observed-" + type, null, null));
        }
        entityManager.flush();

        Integer rows = jdbc.queryForObject(
                "SELECT count(*) FROM reconciliation_exceptions WHERE transaction_id = ?",
                Integer.class, transactionId);

        assertThat(rows)
                .as("the Java enum and the database constraint must agree exactly")
                .isEqualTo(ExceptionType.values().length);
    }

    @Test
    void processorFeeIsRejectedByPostgresEvenWhenTheApplicationIsBypassed() throws Exception {
        String transactionId = createTransaction();
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);

        assertThatThrownBy(() -> jdbc.update(
                "INSERT INTO reconciliation_exceptions (id, exception_id, transaction_id, "
                        + "exception_type, status, detected_at, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), "EX-99999", transactionId, "PROCESSOR_FEE", "OPEN",
                now, now, now))
                .as("the check constraint is the last line of defence if Java is bypassed")
                .isInstanceOf(DataIntegrityViolationException.class)
                .hasMessageContaining("ck_reconciliation_exceptions_type");
    }

    @Test
    void theSameInsertSucceedsWithADeterministicTypeProvingTheConstraintIsWhatRejectsIt()
            throws Exception {
        String transactionId = createTransaction();
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);

        int inserted = jdbc.update(
                "INSERT INTO reconciliation_exceptions (id, exception_id, transaction_id, "
                        + "exception_type, status, detected_at, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), "EX-99998", transactionId, "AMOUNT_MISMATCH", "OPEN",
                now, now, now);

        assertThat(inserted)
                .as("only the exception_type differs from the rejected insert above")
                .isEqualTo(1);
    }

    // -----------------------------------------------------------------
    // Read APIs
    // -----------------------------------------------------------------

    @Test
    void getExceptionByIdReturnsTheContractResponseBody() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        ReconciliationException exception = exceptionService.recordOrReuse(
                amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions/{exceptionId}", exception.getExceptionId()))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.exceptionId").value(exception.getExceptionId()))
                .andExpect(jsonPath("$.transactionId").value(transactionId))
                .andExpect(jsonPath("$.settlementId").value(settlementId))
                .andExpect(jsonPath("$.exceptionType").value("AMOUNT_MISMATCH"))
                .andExpect(jsonPath("$.expectedValue").value("1247.50"))
                .andExpect(jsonPath("$.observedValue").value("1217.50"))
                .andExpect(jsonPath("$.currency").value("USD"))
                .andExpect(jsonPath("$.status").value("OPEN"))
                .andExpect(jsonPath("$.detectedAt").exists())
                .andExpect(content().string(containsString("\"differenceAmount\":30.00")));
    }

    @Test
    void getUnknownExceptionReturns404() throws Exception {
        mockMvc.perform(get("/api/v1/exceptions/{exceptionId}", "EX-1042"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.message")
                        .value("Reconciliation exception EX-1042 was not found."))
                .andExpect(jsonPath("$.path").value("/api/v1/exceptions/EX-1042"))
                .andExpect(jsonPath("$.correlationId").exists());
    }

    @Test
    void apiNeverExposesTheInternalUuid() throws Exception {
        String transactionId = createTransaction();
        ReconciliationException exception = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        entityManager.flush();

        String internalId = jdbc.queryForObject(
                "SELECT id::text FROM reconciliation_exceptions WHERE exception_id = ?",
                String.class, exception.getExceptionId());

        mockMvc.perform(get("/api/v1/exceptions/{exceptionId}", exception.getExceptionId()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").doesNotExist())
                .andExpect(content().string(not(containsString(internalId))));
    }

    @Test
    void listReturnsEveryExceptionWithATotal() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        exceptionService.recordOrReuse(amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.CURRENCY_MISMATCH, settlementId, "USD", "EUR", null, null));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(2)))
                .andExpect(jsonPath("$.total").value(2));
    }

    @Test
    void listIsEmptyWhenNoDiscrepancyHasBeenDetected() throws Exception {
        mockMvc.perform(get("/api/v1/exceptions"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(0)))
                .andExpect(jsonPath("$.total").value(0));
    }

    // -----------------------------------------------------------------
    // Filters
    // -----------------------------------------------------------------

    @Test
    void listCanBeFilteredByStatus() throws Exception {
        String transactionId = createTransaction();
        ReconciliationException open = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions").param("status", "OPEN"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].exceptionId").value(open.getExceptionId()));

        mockMvc.perform(get("/api/v1/exceptions").param("status", "RESOLVED"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(0)));
    }

    @Test
    void listCanBeFilteredByExceptionType() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        ReconciliationException mismatch = exceptionService.recordOrReuse(
                amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.CURRENCY_MISMATCH, settlementId, "USD", "EUR", null, null));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions").param("exceptionType", "AMOUNT_MISMATCH"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].exceptionId").value(mismatch.getExceptionId()));
    }

    @Test
    void listCanBeFilteredByTransactionId() throws Exception {
        String first = createTransaction();
        String second = createTransaction();
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                first, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                second, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions").param("transactionId", first))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].transactionId").value(first));
    }

    @Test
    void filtersCombine() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);
        String other = createTransaction();

        ReconciliationException target = exceptionService.recordOrReuse(
                amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.CURRENCY_MISMATCH, settlementId, "USD", "EUR", null, null));
        exceptionService.recordOrReuse(amountMismatch(other, null, "1217.50", "30.00"));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions")
                        .param("status", "OPEN")
                        .param("exceptionType", "AMOUNT_MISMATCH")
                        .param("transactionId", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].exceptionId").value(target.getExceptionId()));
    }

    @Test
    void combinedFiltersThatMatchNothingReturnAnEmptyList() throws Exception {
        String transactionId = createTransaction();
        exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        entityManager.flush();

        mockMvc.perform(get("/api/v1/exceptions")
                        .param("exceptionType", "DUPLICATE_SETTLEMENT")
                        .param("transactionId", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(0)))
                .andExpect(jsonPath("$.total").value(0));
    }

    @Test
    void anUnknownFilterValueIsACleanValidationErrorRatherThanAServerError() throws Exception {
        mockMvc.perform(get("/api/v1/exceptions").param("exceptionType", "PROCESSOR_FEE"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString(
                        "exceptionType must be one of [AMOUNT_MISMATCH, MISSING_SETTLEMENT, "
                                + "DUPLICATE_SETTLEMENT, CURRENCY_MISMATCH]")));
    }

    @Test
    void listIsOrderedByDetectionTimeDescendingWithTheExceptionIdBreakingTies()
            throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId);

        ReconciliationException first = exceptionService.recordOrReuse(
                amountMismatch(transactionId, settlementId, "1217.50", "30.00"));
        ReconciliationException second = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.CURRENCY_MISMATCH, settlementId, "USD", "EUR", null, null));
        ReconciliationException third = exceptionService.recordOrReuse(new DetectedDiscrepancy(
                transactionId, ExceptionType.MISSING_SETTLEMENT, null,
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, null));
        entityManager.flush();

        String firstRun = mockMvc.perform(get("/api/v1/exceptions"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(3)))
                .andReturn().getResponse().getContentAsString();
        String secondRun = mockMvc.perform(get("/api/v1/exceptions"))
                .andReturn().getResponse().getContentAsString();

        assertThat(firstRun)
                .as("identical requests must return an identical sequence")
                .isEqualTo(secondRun);
        assertThat(firstRun).contains(first.getExceptionId(), second.getExceptionId(),
                third.getExceptionId());
    }

    // -----------------------------------------------------------------
    // Stage 4 must not detect anything
    // -----------------------------------------------------------------

    @Test
    void creatingATransactionCreatesNoReconciliationException() throws Exception {
        createTransaction();
        entityManager.flush();

        assertThat(countExceptions())
                .as("detection happens only when reconciliation is run")
                .isZero();
    }

    @Test
    void creatingASettlementCreatesNoReconciliationExceptionEvenWhenAmountsDisagree()
            throws Exception {
        String transactionId = createTransaction();
        mockMvc.perform(post("/api/v1/settlements")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "transactionId": "%s",
                                  "processor": "NORTHSTAR_PAYMENTS",
                                  "settledAmount": 1217.50,
                                  "currency": "EUR",
                                  "status": "COMPLETED",
                                  "settlementTimestamp": "2026-09-26T14:00:00Z"
                                }
                                """.formatted(transactionId)))
                .andExpect(status().isCreated());
        entityManager.flush();

        assertThat(countExceptions())
                .as("a settlement that disagrees on both amount and currency is still "
                        + "only ingested data until reconciliation runs")
                .isZero();
    }

    @Test
    void duplicateSettlementsAloneCreateNoReconciliationException() throws Exception {
        String transactionId = createTransaction();
        createSettlement(transactionId);
        createSettlement(transactionId);
        entityManager.flush();

        assertThat(countExceptions()).isZero();
    }

    // -----------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------

    private static DetectedDiscrepancy amountMismatch(String transactionId, String settlementId,
                                                      String observed, String difference) {
        return new DetectedDiscrepancy(transactionId, ExceptionType.AMOUNT_MISMATCH, settlementId,
                "1247.50", observed, new BigDecimal(difference), "USD");
    }

    private Integer countExceptions() {
        return jdbc.queryForObject("SELECT count(*) FROM reconciliation_exceptions", Integer.class);
    }

    private String createTransaction() throws Exception {
        String body = mockMvc.perform(post("/api/v1/transactions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "merchantId": "MERCHANT-104",
                                  "amount": 1247.50,
                                  "expectedSettlementAmount": 1247.50,
                                  "currency": "USD",
                                  "transactionType": "PURCHASE",
                                  "status": "POSTED",
                                  "transactionTimestamp": "2026-09-26T13:45:00Z"
                                }
                                """))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        entityManager.flush();
        return com.jayway.jsonpath.JsonPath.read(body, "$.transactionId");
    }

    private String createSettlement(String transactionId) throws Exception {
        String body = mockMvc.perform(post("/api/v1/settlements")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "transactionId": "%s",
                                  "processor": "NORTHSTAR_PAYMENTS",
                                  "settledAmount": 1217.50,
                                  "currency": "USD",
                                  "status": "COMPLETED",
                                  "settlementTimestamp": "2026-09-26T14:00:00Z"
                                }
                                """.formatted(transactionId)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        entityManager.flush();
        return com.jayway.jsonpath.JsonPath.read(body, "$.settlementId");
    }
}
