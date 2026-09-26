package com.reconai.settlement;

import com.reconai.support.PostgresIntegrationTest;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.matchesPattern;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * End-to-end tests for the settlement endpoints against a real PostgreSQL instance.
 *
 * <p>Several tests exist specifically to prove that a transaction may accumulate more
 * than one settlement. That is not an oversight in the model: without it the
 * DUPLICATE_SETTLEMENT reconciliation rule could never fire.
 */
@AutoConfigureMockMvc
@Transactional
class SettlementApiIntegrationTest extends PostgresIntegrationTest {

    private static final String TRANSACTION_REQUEST = """
            {
              "merchantId": "MERCHANT-104",
              "amount": 1247.50,
              "expectedSettlementAmount": 1247.50,
              "currency": "USD",
              "transactionType": "PURCHASE",
              "status": "POSTED",
              "transactionTimestamp": "2026-09-26T13:45:00Z"
            }
            """;

    private final MockMvc mockMvc;
    private final JdbcTemplate jdbc;
    private final EntityManager entityManager;

    @Autowired
    SettlementApiIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc, EntityManager entityManager) {
        this.mockMvc = mockMvc;
        this.jdbc = jdbc;
        this.entityManager = entityManager;
    }

    // -----------------------------------------------------------------
    // Creation
    // -----------------------------------------------------------------

    @Test
    void createSettlementReturns201WithTheContractResponseBody() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.50", "USD", "COMPLETED")))
                .andExpect(status().isCreated())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.transactionId").value(transactionId))
                .andExpect(jsonPath("$.processor").value("NORTHSTAR_PAYMENTS"))
                .andExpect(jsonPath("$.currency").value("USD"))
                .andExpect(jsonPath("$.status").value("COMPLETED"))
                .andExpect(jsonPath("$.settlementTimestamp").value("2026-09-26T14:00:00Z"));
    }

    @Test
    void createdSettlementReceivesAGeneratedSetBusinessId() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.50", "USD", "COMPLETED")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.settlementId").value(matchesPattern("^SET-\\d+$")));
    }

    @Test
    void consecutiveSettlementsReceiveDistinctBusinessIds() throws Exception {
        String transactionId = createTransaction();

        String first = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");
        String second = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        assertThat(first).isNotEqualTo(second);
    }

    @Test
    void businessIdIsSeparateFromTheInternalUuidPrimaryKey() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        UUID internalId = jdbc.queryForObject(
                "SELECT id FROM settlements WHERE settlement_id = ?", UUID.class, settlementId);

        assertThat(internalId).isNotNull();
        assertThat(internalId.toString()).isNotEqualTo(settlementId);
        assertThat(settlementId).startsWith("SET-");
    }

    @Test
    void apiNeverExposesTheInternalUuid() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");
        String internalId = jdbc.queryForObject(
                "SELECT id::text FROM settlements WHERE settlement_id = ?", String.class, settlementId);

        mockMvc.perform(get("/api/v1/transactions/{transactionId}/settlements", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.settlements[0].id").doesNotExist())
                .andExpect(content().string(not(containsString(internalId))));
    }

    @Test
    void creatingASettlementDoesNotAlterTheTransactionStatus() throws Exception {
        String transactionId = createTransaction();
        createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        String status = jdbc.queryForObject(
                "SELECT status FROM transactions WHERE transaction_id = ?",
                String.class, transactionId);

        assertThat(status)
                .as("ingesting a settlement must not infer anything about the transaction")
                .isEqualTo("POSTED");
    }

    @Test
    void creatingASettlementCreatesNoReconciliationException() throws Exception {
        String transactionId = createTransaction();
        createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        Integer exceptions = jdbc.queryForObject(
                "SELECT count(*) FROM reconciliation_exceptions WHERE transaction_id = ?",
                Integer.class, transactionId);

        assertThat(exceptions)
                .as("a discrepancy is only established when reconciliation is run")
                .isZero();
    }

    // -----------------------------------------------------------------
    // Multiple settlements per transaction
    // -----------------------------------------------------------------

    @Test
    void multipleCompletedSettlementsMayExistForOneTransaction() throws Exception {
        String transactionId = createTransaction();

        String first = createSettlement(transactionId, "850.00", "USD", "COMPLETED");
        String second = createSettlement(transactionId, "850.00", "USD", "COMPLETED");

        Integer completed = jdbc.queryForObject(
                "SELECT count(*) FROM settlements WHERE transaction_id = ? AND status = 'COMPLETED'",
                Integer.class, transactionId);

        assertThat(completed)
                .as("DUPLICATE_SETTLEMENT detection in a later stage depends on this")
                .isEqualTo(2);
        assertThat(first).isNotEqualTo(second);
    }

    @Test
    void allSettlementsForATransactionAreReturned() throws Exception {
        String transactionId = createTransaction();
        createSettlement(transactionId, "850.00", "USD", "COMPLETED");
        createSettlement(transactionId, "850.00", "USD", "COMPLETED");
        createSettlement(transactionId, "100.00", "USD", "FAILED");

        mockMvc.perform(get("/api/v1/transactions/{transactionId}/settlements", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.transactionId").value(transactionId))
                .andExpect(jsonPath("$.settlements", hasSize(3)));
    }

    @Test
    void settlementsAreReturnedInDeterministicTimestampOrder() throws Exception {
        String transactionId = createTransaction();
        String later = createSettlement(transactionId, "10.00", "USD", "COMPLETED",
                "2026-09-26T16:00:00Z");
        String earlier = createSettlement(transactionId, "20.00", "USD", "COMPLETED",
                "2026-09-26T09:00:00Z");

        mockMvc.perform(get("/api/v1/transactions/{transactionId}/settlements", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.settlements[0].settlementId").value(earlier))
                .andExpect(jsonPath("$.settlements[1].settlementId").value(later));
    }

    @Test
    void settlementsSharingATimestampAreOrderedByIdentifierAsAStableTieBreaker()
            throws Exception {
        String transactionId = createTransaction();
        String first = createSettlement(transactionId, "10.00", "USD", "COMPLETED");
        String second = createSettlement(transactionId, "20.00", "USD", "COMPLETED");

        String firstReturned = mockMvc.perform(
                        get("/api/v1/transactions/{transactionId}/settlements", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.settlements", hasSize(2)))
                .andReturn().getResponse().getContentAsString();

        assertThat(firstReturned.indexOf(first))
                .as("ordering must be stable across runs, not left to the database")
                .isLessThan(firstReturned.indexOf(second));
    }

    // -----------------------------------------------------------------
    // Retrieval edge cases
    // -----------------------------------------------------------------

    @Test
    void transactionWithNoSettlementsReturnsAnEmptyArrayRatherThan404() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(get("/api/v1/transactions/{transactionId}/settlements", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.transactionId").value(transactionId))
                .andExpect(jsonPath("$.settlements").isArray())
                .andExpect(jsonPath("$.settlements", hasSize(0)));
    }

    @Test
    void getSettlementsForUnknownTransactionReturns404() throws Exception {
        mockMvc.perform(get("/api/v1/transactions/{transactionId}/settlements", "TX-48291"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.message").value("Transaction TX-48291 was not found."))
                .andExpect(jsonPath("$.path").value("/api/v1/transactions/TX-48291/settlements"))
                .andExpect(jsonPath("$.correlationId").exists());
    }

    @Test
    void creatingASettlementForAnUnknownTransactionReturns404() throws Exception {
        mockMvc.perform(postSettlement(settlementRequest("TX-48291", "1217.50", "USD", "COMPLETED")))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.message").value("Transaction TX-48291 was not found."))
                .andExpect(jsonPath("$.path").value("/api/v1/settlements"));
    }

    @Test
    void rejectedSettlementForUnknownTransactionIsNotPersisted() throws Exception {
        mockMvc.perform(postSettlement(settlementRequest("TX-48291", "1217.50", "USD", "COMPLETED")))
                .andExpect(status().isNotFound());

        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM settlements WHERE transaction_id = 'TX-48291'", Integer.class);

        assertThat(count).isZero();
    }

    // -----------------------------------------------------------------
    // Validation
    // -----------------------------------------------------------------

    @Test
    void negativeSettledAmountIsRejected() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "-0.01", "USD", "COMPLETED")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message")
                        .value(containsString("settledAmount must be greater than or equal to 0")));
    }

    @Test
    void settledAmountWithMoreThanFourDecimalPlacesIsRejectedRatherThanSilentlyRounded()
            throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.56789", "USD", "COMPLETED")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString("4 decimal places")));
    }

    @Test
    void currencyWithWrongLengthIsRejected() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.50", "US", "COMPLETED")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value(containsString("currency must be exactly 3 characters")));

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.50", "USDX", "COMPLETED")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void lowercaseCurrencyIsNormalisedToUppercase() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.50", "usd", "COMPLETED")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.currency").value("USD"));
    }

    @Test
    void missingRequiredFieldsAreRejectedAndEveryMissingFieldIsNamed() throws Exception {
        mockMvc.perform(postSettlement("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString("transactionId is required")))
                .andExpect(jsonPath("$.message").value(containsString("processor is required")))
                .andExpect(jsonPath("$.message").value(containsString("settledAmount is required")))
                .andExpect(jsonPath("$.message").value(containsString("currency is required")))
                .andExpect(jsonPath("$.message").value(containsString("status is required")))
                .andExpect(jsonPath("$.message").value(containsString("settlementTimestamp is required")));
    }

    @Test
    void missingProcessorIsRejected() throws Exception {
        String transactionId = createTransaction();
        String request = settlementRequest(transactionId, "1217.50", "USD", "COMPLETED")
                .replace("\"processor\": \"NORTHSTAR_PAYMENTS\",", "");

        mockMvc.perform(postSettlement(request))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value(containsString("processor is required")));
    }

    @Test
    void blankProcessorIsRejected() throws Exception {
        String transactionId = createTransaction();
        String request = settlementRequest(transactionId, "1217.50", "USD", "COMPLETED")
                .replace("\"NORTHSTAR_PAYMENTS\"", "\"   \"");

        mockMvc.perform(postSettlement(request))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value(containsString("processor is required")));
    }

    @Test
    void invalidSettlementStatusIsACleanValidationErrorRatherThanAServerError() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.50", "USD", "SETTLED")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString(
                        "status must be one of [PENDING, COMPLETED, REVERSED, FAILED]")));
    }

    // -----------------------------------------------------------------
    // Financial precision
    // -----------------------------------------------------------------

    @Test
    void settledAmountPreservesPrecisionToFourDecimalPlaces() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1217.5678", "USD", "COMPLETED")))
                .andExpect(status().isCreated())
                .andExpect(content().string(containsString("\"settledAmount\":1217.5678")));

        mockMvc.perform(get("/api/v1/transactions/{transactionId}/settlements", transactionId))
                .andExpect(content().string(containsString("\"settledAmount\":1217.5678")));
    }

    @Test
    void settledAmountIsSerialisedWithAtLeastTwoDecimalPlaces() throws Exception {
        String transactionId = createTransaction();

        mockMvc.perform(postSettlement(settlementRequest(transactionId, "1200", "USD", "COMPLETED")))
                .andExpect(status().isCreated())
                .andExpect(content().string(containsString("\"settledAmount\":1200.00")));
    }

    @Test
    void storedSettledAmountMatchesTheNumericColumnScale() throws Exception {
        String transactionId = createTransaction();
        String settlementId = createSettlement(transactionId, "1217.50", "USD", "COMPLETED");

        String stored = jdbc.queryForObject(
                "SELECT settled_amount::text FROM settlements WHERE settlement_id = ?",
                String.class, settlementId);

        assertThat(stored).isEqualTo("1217.5000");
    }

    // -----------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------

    private static MockHttpServletRequestBuilder postSettlement(String body) {
        return post("/api/v1/settlements").contentType(MediaType.APPLICATION_JSON).content(body);
    }

    private static String settlementRequest(String transactionId, String settledAmount,
                                            String currency, String status) {
        return settlementRequest(transactionId, settledAmount, currency, status,
                "2026-09-26T14:00:00Z");
    }

    private static String settlementRequest(String transactionId, String settledAmount,
                                            String currency, String status, String timestamp) {
        return """
                {
                  "transactionId": "%s",
                  "processor": "NORTHSTAR_PAYMENTS",
                  "settledAmount": %s,
                  "currency": "%s",
                  "status": "%s",
                  "settlementTimestamp": "%s"
                }
                """.formatted(transactionId, settledAmount, currency, status, timestamp);
    }

    /** Creates a transaction and returns its business identifier. */
    private String createTransaction() throws Exception {
        String transactionId = readId(mockMvc.perform(post("/api/v1/transactions")
                .contentType(MediaType.APPLICATION_JSON)
                .content(TRANSACTION_REQUEST))
                .andExpect(status().isCreated())
                .andReturn(), "$.transactionId");
        entityManager.flush();
        return transactionId;
    }

    /**
     * Creates a settlement and returns its business identifier.
     *
     * <p>The flush is needed only because the whole test runs inside one transaction
     * that is rolled back at the end; in production each request commits its own.
     */
    private String createSettlement(String transactionId, String settledAmount,
                                    String currency, String status) throws Exception {
        return createSettlement(transactionId, settledAmount, currency, status,
                "2026-09-26T14:00:00Z");
    }

    private String createSettlement(String transactionId, String settledAmount, String currency,
                                    String status, String timestamp) throws Exception {
        String settlementId = readId(mockMvc.perform(postSettlement(
                settlementRequest(transactionId, settledAmount, currency, status, timestamp)))
                .andExpect(status().isCreated())
                .andReturn(), "$.settlementId");
        entityManager.flush();
        return settlementId;
    }

    private String readId(MvcResult result, String path) throws Exception {
        return com.jayway.jsonpath.JsonPath.read(result.getResponse().getContentAsString(), path);
    }
}
