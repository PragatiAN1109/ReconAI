package com.reconai.transaction;

import com.reconai.support.PostgresIntegrationTest;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.matchesPattern;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * End-to-end tests for the transaction endpoints against a real PostgreSQL instance.
 *
 * <p>Requests and assertions use raw JSON so that the wire format defined in
 * docs/api-contract.md is what is actually verified.
 */
@AutoConfigureMockMvc
@Transactional
class TransactionApiIntegrationTest extends PostgresIntegrationTest {

    private static final String VALID_REQUEST = """
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
    TransactionApiIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc, EntityManager entityManager) {
        this.mockMvc = mockMvc;
        this.jdbc = jdbc;
        this.entityManager = entityManager;
    }

    // -----------------------------------------------------------------
    // Creation
    // -----------------------------------------------------------------

    @Test
    void createTransactionReturns201WithTheContractResponseBody() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST))
                .andExpect(status().isCreated())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.merchantId").value("MERCHANT-104"))
                .andExpect(jsonPath("$.currency").value("USD"))
                .andExpect(jsonPath("$.transactionType").value("PURCHASE"))
                .andExpect(jsonPath("$.status").value("POSTED"))
                .andExpect(jsonPath("$.transactionTimestamp").value("2026-09-26T13:45:00Z"))
                .andExpect(jsonPath("$.createdAt").exists());
    }

    @Test
    void createdTransactionReceivesAGeneratedTxBusinessId() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.transactionId").value(matchesPattern("^TX-\\d+$")));
    }

    @Test
    void consecutiveTransactionsReceiveDistinctBusinessIds() throws Exception {
        String first = createTransactionAndReturnId();
        String second = createTransactionAndReturnId();

        assertThat(first).isNotEqualTo(second);
    }

    @Test
    void businessIdIsSeparateFromTheInternalUuidPrimaryKey() throws Exception {
        String transactionId = createTransactionAndReturnId();

        UUID internalId = jdbc.queryForObject(
                "SELECT id FROM transactions WHERE transaction_id = ?", UUID.class, transactionId);

        assertThat(internalId).isNotNull();
        assertThat(internalId.toString()).isNotEqualTo(transactionId);
        assertThat(transactionId).startsWith("TX-");
    }

    @Test
    void apiNeverExposesTheInternalUuid() throws Exception {
        String transactionId = createTransactionAndReturnId();
        String internalId = jdbc.queryForObject(
                "SELECT id::text FROM transactions WHERE transaction_id = ?", String.class, transactionId);

        mockMvc.perform(get("/api/v1/transactions/{transactionId}", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").doesNotExist())
                .andExpect(content().string(not(containsString(internalId))));
    }

    @Test
    void transactionTypeAndStatusArePersistedAsEnumNamesAcceptedByTheCheckConstraints()
            throws Exception {
        String transactionId = createTransactionAndReturnId();

        String type = jdbc.queryForObject(
                "SELECT transaction_type FROM transactions WHERE transaction_id = ?",
                String.class, transactionId);
        String status = jdbc.queryForObject(
                "SELECT status FROM transactions WHERE transaction_id = ?",
                String.class, transactionId);

        assertThat(type).isEqualTo("PURCHASE");
        assertThat(status).isEqualTo("POSTED");
    }

    @Test
    void currencyIsNormalisedToUppercaseSoReconciliationDoesNotSeeAFalseMismatch() throws Exception {
        String request = VALID_REQUEST.replace("\"USD\"", "\"usd\"");

        mockMvc.perform(postTransaction(request))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.currency").value("USD"));
    }

    // -----------------------------------------------------------------
    // Retrieval
    // -----------------------------------------------------------------

    @Test
    void getReturnsThePreviouslyCreatedTransaction() throws Exception {
        String transactionId = createTransactionAndReturnId();

        mockMvc.perform(get("/api/v1/transactions/{transactionId}", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.transactionId").value(transactionId))
                .andExpect(jsonPath("$.merchantId").value("MERCHANT-104"))
                .andExpect(jsonPath("$.currency").value("USD"));
    }

    @Test
    void getUnknownTransactionReturns404WithTheContractErrorBody() throws Exception {
        mockMvc.perform(get("/api/v1/transactions/{transactionId}", "TX-48291"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status").value(404))
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.message").value("Transaction TX-48291 was not found."))
                .andExpect(jsonPath("$.path").value("/api/v1/transactions/TX-48291"))
                .andExpect(jsonPath("$.timestamp").exists())
                .andExpect(jsonPath("$.correlationId").exists());
    }

    // -----------------------------------------------------------------
    // Validation
    // -----------------------------------------------------------------

    @Test
    void negativeAmountIsRejected() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST.replace("\"amount\": 1247.50", "\"amount\": -0.01")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message")
                        .value(containsString("amount must be greater than or equal to 0")));
    }

    @Test
    void negativeExpectedSettlementAmountIsRejected() throws Exception {
        String request = VALID_REQUEST.replace(
                "\"expectedSettlementAmount\": 1247.50", "\"expectedSettlementAmount\": -1");

        mockMvc.perform(postTransaction(request))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(
                        containsString("expectedSettlementAmount must be greater than or equal to 0")));
    }

    @Test
    void zeroAmountsAreAcceptedBecauseTheContractRequiresAtLeastZeroNotAboveZero() throws Exception {
        String request = VALID_REQUEST
                .replace("\"amount\": 1247.50", "\"amount\": 0")
                .replace("\"expectedSettlementAmount\": 1247.50", "\"expectedSettlementAmount\": 0");

        mockMvc.perform(postTransaction(request)).andExpect(status().isCreated());
    }

    @Test
    void currencyShorterThanThreeCharactersIsRejected() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST.replace("\"USD\"", "\"US\"")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString("currency must be exactly 3 characters")));
    }

    @Test
    void currencyLongerThanThreeCharactersIsRejected() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST.replace("\"USD\"", "\"USDX\"")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void missingRequiredFieldsAreRejectedAndEveryMissingFieldIsNamed() throws Exception {
        mockMvc.perform(postTransaction("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString("merchantId is required")))
                .andExpect(jsonPath("$.message").value(containsString("amount is required")))
                .andExpect(jsonPath("$.message").value(containsString("expectedSettlementAmount is required")))
                .andExpect(jsonPath("$.message").value(containsString("currency is required")))
                .andExpect(jsonPath("$.message").value(containsString("transactionType is required")))
                .andExpect(jsonPath("$.message").value(containsString("status is required")))
                .andExpect(jsonPath("$.message").value(containsString("transactionTimestamp is required")));
    }

    @Test
    void unknownTransactionTypeIsAValidationErrorRatherThanAServerError() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST.replace("\"PURCHASE\"", "\"PROCESSOR_FEE\"")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(
                        containsString("transactionType must be one of [PURCHASE, REFUND, REVERSAL]")));
    }

    @Test
    void unknownTransactionStatusIsAValidationError() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST.replace("\"POSTED\"", "\"NOT_A_STATUS\"")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message").value(containsString(
                        "status must be one of [AUTHORIZED, POSTED, SETTLED, REVERSED]")));
    }

    @Test
    void amountWithMoreThanFourDecimalPlacesIsRejectedRatherThanSilentlyRounded() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST.replace("\"amount\": 1247.50", "\"amount\": 1247.56789")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value(containsString("4 decimal places")));
    }

    @Test
    void malformedJsonIsRejectedWithoutLeakingParserDetail() throws Exception {
        mockMvc.perform(postTransaction("{ not json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value("Request body is missing or malformed."));
    }

    // -----------------------------------------------------------------
    // Financial precision
    // -----------------------------------------------------------------

    @Test
    void monetaryValuesPreservePrecisionToFourDecimalPlaces() throws Exception {
        String request = VALID_REQUEST
                .replace("\"amount\": 1247.50", "\"amount\": 1217.5678")
                .replace("\"expectedSettlementAmount\": 1247.50",
                        "\"expectedSettlementAmount\": 1217.5678");

        String transactionId = idOf(mockMvc.perform(postTransaction(request))
                .andExpect(status().isCreated())
                .andExpect(content().string(containsString("\"amount\":1217.5678")))
                .andReturn());

        mockMvc.perform(get("/api/v1/transactions/{transactionId}", transactionId))
                .andExpect(content().string(containsString("\"amount\":1217.5678")));
    }

    @Test
    void monetaryValuesAreSerialisedWithAtLeastTwoDecimalPlaces() throws Exception {
        String request = VALID_REQUEST
                .replace("\"amount\": 1247.50", "\"amount\": 1247.5")
                .replace("\"expectedSettlementAmount\": 1247.50", "\"expectedSettlementAmount\": 1200");

        mockMvc.perform(postTransaction(request))
                .andExpect(status().isCreated())
                .andExpect(content().string(containsString("\"amount\":1247.50")))
                .andExpect(content().string(containsString("\"expectedSettlementAmount\":1200.00")));
    }

    @Test
    void storedAndReturnedAmountsAgreeExactlyWithTheNumericColumn() throws Exception {
        String transactionId = createTransactionAndReturnId();

        String stored = jdbc.queryForObject(
                "SELECT amount::text FROM transactions WHERE transaction_id = ?",
                String.class, transactionId);

        assertThat(stored)
                .as("NUMERIC(19,4) storage scale")
                .isEqualTo("1247.5000");

        mockMvc.perform(get("/api/v1/transactions/{transactionId}", transactionId))
                .andExpect(content().string(containsString("\"amount\":1247.50")));
    }

    @Test
    void largeAmountsAreNeverSerialisedInScientificNotation() throws Exception {
        String request = VALID_REQUEST
                .replace("\"amount\": 1247.50", "\"amount\": 999999999999.99")
                .replace("\"expectedSettlementAmount\": 1247.50",
                        "\"expectedSettlementAmount\": 999999999999.99");

        mockMvc.perform(postTransaction(request))
                .andExpect(status().isCreated())
                .andExpect(content().string(containsString("\"amount\":999999999999.99")))
                .andExpect(content().string(not(containsString("E+"))));
    }

    // -----------------------------------------------------------------
    // Correlation ID and error hygiene
    // -----------------------------------------------------------------

    @Test
    void correlationIdIsGeneratedAndReturnedWhenTheCallerSuppliesNone() throws Exception {
        mockMvc.perform(postTransaction(VALID_REQUEST))
                .andExpect(status().isCreated())
                .andExpect(header().string("X-Correlation-ID", matchesPattern("^CORR-[0-9A-F]{8}$")));
    }

    @Test
    void suppliedCorrelationIdIsEchoedAndAppearsInErrorResponses() throws Exception {
        mockMvc.perform(get("/api/v1/transactions/{transactionId}", "TX-99999")
                        .header("X-Correlation-ID", "CORR-89123"))
                .andExpect(status().isNotFound())
                .andExpect(header().string("X-Correlation-ID", "CORR-89123"))
                .andExpect(jsonPath("$.correlationId").value("CORR-89123"));
    }

    @Test
    void unacceptableCorrelationIdIsReplacedRatherThanEchoedBack() throws Exception {
        mockMvc.perform(get("/api/v1/transactions/{transactionId}", "TX-99999")
                        .header("X-Correlation-ID", "bad value with spaces"))
                .andExpect(status().isNotFound())
                .andExpect(header().string("X-Correlation-ID", matchesPattern("^CORR-[0-9A-F]{8}$")));
    }

    @Test
    void errorResponsesNeverLeakStackTracesOrDatabaseDetail() throws Exception {
        String body = mockMvc.perform(get("/api/v1/transactions/{transactionId}", "TX-99999"))
                .andExpect(status().isNotFound())
                .andReturn().getResponse().getContentAsString();

        assertThat(body)
                .doesNotContain("Exception")
                .doesNotContain("org.springframework")
                .doesNotContain("com.reconai")
                .doesNotContain("SELECT")
                .doesNotContain("trace");
    }

    // -----------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------

    private static org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder
            postTransaction(String body) {
        return post("/api/v1/transactions")
                .contentType(MediaType.APPLICATION_JSON)
                .content(body);
    }

    /**
     * Creates a transaction and flushes the persistence context.
     *
     * <p>The flush matters only here: the whole test runs in one transaction that is
     * rolled back at the end, so without it Hibernate's pending INSERT would not yet be
     * visible to the raw SQL assertions below. In production each request commits its
     * own transaction and no flush is needed.
     */
    private String createTransactionAndReturnId() throws Exception {
        String transactionId = idOf(mockMvc.perform(postTransaction(VALID_REQUEST))
                .andExpect(status().isCreated())
                .andReturn());
        entityManager.flush();
        return transactionId;
    }

    private String idOf(MvcResult result) throws Exception {
        return com.jayway.jsonpath.JsonPath.read(
                result.getResponse().getContentAsString(), "$.transactionId");
    }
}
