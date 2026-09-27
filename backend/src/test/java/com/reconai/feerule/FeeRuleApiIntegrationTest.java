package com.reconai.feerule;

import com.reconai.exception.ExceptionType;
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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Tests the fee rule schema and read endpoint.
 *
 * <p>Fee rules are reference data with no create path, so these tests insert directly
 * and then read through the API — the same shape as the seed migration.
 */
@AutoConfigureMockMvc
@Transactional
class FeeRuleApiIntegrationTest extends PostgresIntegrationTest {

    private static final OffsetDateTime NOW = OffsetDateTime.now(ZoneOffset.UTC);

    private final MockMvc mockMvc;
    private final JdbcTemplate jdbc;
    private final EntityManager entityManager;

    @Autowired
    FeeRuleApiIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc, EntityManager entityManager) {
        this.mockMvc = mockMvc;
        this.jdbc = jdbc;
        this.entityManager = entityManager;
    }

    // -----------------------------------------------------------------
    // Persistence
    // -----------------------------------------------------------------

    @Test
    void aFeeRuleIsPersistedWithItsBusinessIdentifier() {
        insertFeeRule("FR-14", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);

        String stored = jdbc.queryForObject(
                "SELECT rule_id FROM fee_rules WHERE rule_id = 'FR-14'", String.class);

        assertThat(stored).isEqualTo("FR-14");
    }

    @Test
    void feeAmountsArePersistedAtNumericScaleFourAndNeverAsFloatingPoint() {
        insertFeeRule("FR-20", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);

        String stored = jdbc.queryForObject(
                "SELECT fee_amount::text FROM fee_rules WHERE rule_id = 'FR-20'", String.class);

        assertThat(stored).isEqualTo("50.0000");
    }

    @Test
    void anAmountThatFloatingPointWouldCorruptIsStoredExactly() {
        insertFeeRule("FR-21", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "0.1", "USD", true);

        BigDecimal stored = jdbc.queryForObject(
                "SELECT fee_amount FROM fee_rules WHERE rule_id = 'FR-21'", BigDecimal.class);

        assertThat(stored).isEqualByComparingTo("0.1");
    }

    @Test
    void feeRuleBusinessIdentifiersAreUnique() {
        insertFeeRule("FR-22", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);

        assertThatThrownBy(() ->
                insertFeeRule("FR-22", null, "ATLAS_CLEARING", "NETWORK", "1.00", "USD", true))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void aNegativeFeeAmountIsRejected() {
        assertThatThrownBy(() ->
                insertFeeRule("FR-23", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "-1.00", "USD", true))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void anUnknownFeeTypeIsRejectedByTheDatabase() {
        assertThatThrownBy(() ->
                insertFeeRule("FR-24", null, "NORTHSTAR_PAYMENTS", "NOT_A_FEE_TYPE", "1.00", "USD", true))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void aRuleMayApplyToEveryMerchantOnAProcessor() {
        insertFeeRule("FR-25", null, "NORTHSTAR_PAYMENTS", "NETWORK", "2.50", "USD", true);

        String merchant = jdbc.queryForObject(
                "SELECT merchant_id FROM fee_rules WHERE rule_id = 'FR-25'", String.class);

        assertThat(merchant).isNull();
    }

    // -----------------------------------------------------------------
    // Read endpoint
    // -----------------------------------------------------------------

    @Test
    void theEndpointReturnsAStoredRule() throws Exception {
        insertFeeRule("FR-14", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.total").value(1))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-14"))
                .andExpect(jsonPath("$.items[0].merchantId").value("MERCHANT-104"))
                .andExpect(jsonPath("$.items[0].processor").value("NORTHSTAR_PAYMENTS"))
                .andExpect(jsonPath("$.items[0].feeType").value("PROCESSING"))
                .andExpect(jsonPath("$.items[0].currency").value("USD"))
                .andExpect(jsonPath("$.items[0].active").value(true))
                .andExpect(content().string(containsString("\"feeAmount\":50.00")));
    }

    @Test
    void theEndpointDoesNotExposeTheInternalUuid() throws Exception {
        insertFeeRule("FR-26", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        entityManager.flush();

        String internalId = jdbc.queryForObject(
                "SELECT id::text FROM fee_rules WHERE rule_id = 'FR-26'", String.class);

        mockMvc.perform(get("/api/v1/fee-rules"))
                .andExpect(status().isOk())
                .andExpect(content().string(org.hamcrest.Matchers.not(containsString(internalId))));
    }

    @Test
    void anEmptyTableReturnsAnEmptyList() throws Exception {
        mockMvc.perform(get("/api/v1/fee-rules"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(0)))
                .andExpect(jsonPath("$.total").value(0));
    }

    @Test
    void rulesAreReturnedInDeterministicOrder() throws Exception {
        insertFeeRule("FR-31", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "1.00", "USD", true);
        insertFeeRule("FR-30", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "2.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules"))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-30"))
                .andExpect(jsonPath("$.items[1].ruleId").value("FR-31"));
    }

    // -----------------------------------------------------------------
    // Filters
    // -----------------------------------------------------------------

    @Test
    void rulesCanBeFilteredByProcessor() throws Exception {
        insertFeeRule("FR-40", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        insertFeeRule("FR-41", null, "ATLAS_CLEARING", "PROCESSING", "35.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules").param("processor", "NORTHSTAR_PAYMENTS"))
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-40"));
    }

    @Test
    void rulesCanBeFilteredByCurrency() throws Exception {
        insertFeeRule("FR-42", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        insertFeeRule("FR-43", null, "NORTHSTAR_PAYMENTS", "CROSS_BORDER", "12.00", "EUR", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules").param("currency", "EUR"))
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-43"));
    }

    @Test
    void rulesCanBeFilteredByActiveState() throws Exception {
        insertFeeRule("FR-44", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        insertFeeRule("FR-45", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", false);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules").param("active", "true"))
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-44"));

        mockMvc.perform(get("/api/v1/fee-rules").param("active", "false"))
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-45"));
    }

    @Test
    void aMerchantFilterAlsoReturnsRulesThatApplyToEveryMerchant() throws Exception {
        insertFeeRule("FR-50", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        insertFeeRule("FR-51", null, "NORTHSTAR_PAYMENTS", "NETWORK", "2.50", "USD", true);
        insertFeeRule("FR-52", "MERCHANT-OTHER", "NORTHSTAR_PAYMENTS", "PROCESSING", "9.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules").param("merchantId", "MERCHANT-104"))
                .andExpect(jsonPath("$.items", hasSize(2)))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-50"))
                .andExpect(jsonPath("$.items[1].ruleId").value("FR-51"));
    }

    @Test
    void filtersCombine() throws Exception {
        insertFeeRule("FR-60", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        insertFeeRule("FR-61", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", false);
        insertFeeRule("FR-62", "MERCHANT-104", "ATLAS_CLEARING", "PROCESSING", "35.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules")
                        .param("merchantId", "MERCHANT-104")
                        .param("processor", "NORTHSTAR_PAYMENTS")
                        .param("currency", "USD")
                        .param("active", "true"))
                .andExpect(jsonPath("$.items", hasSize(1)))
                .andExpect(jsonPath("$.items[0].ruleId").value("FR-60"));
    }

    @Test
    void filtersThatMatchNothingReturnAnEmptyList() throws Exception {
        insertFeeRule("FR-70", null, "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(get("/api/v1/fee-rules").param("processor", "NOBODY"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items", hasSize(0)));
    }

    // -----------------------------------------------------------------
    // The endpoint is read-only
    // -----------------------------------------------------------------

    @Test
    void feeRulesCannotBeCreatedOrChangedThroughTheApi() throws Exception {
        String body = "{\"ruleId\":\"FR-99\"}";

        // The collection exists but supports only GET, which is 405 rather than 400.
        mockMvc.perform(post("/api/v1/fee-rules").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(put("/api/v1/fee-rules").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(patch("/api/v1/fee-rules").contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(delete("/api/v1/fee-rules"))
                .andExpect(status().isMethodNotAllowed());

        Integer created = jdbc.queryForObject(
                "SELECT count(*) FROM fee_rules WHERE rule_id = 'FR-99'", Integer.class);
        assertThat(created).isZero();
    }

    @Test
    void theOnlyMappedFeeRuleOperationIsARead() {
        long writeMappings = java.util.Arrays.stream(FeeRuleController.class.getDeclaredMethods())
                .flatMap(method -> java.util.Arrays.stream(method.getAnnotations()))
                .map(annotation -> annotation.annotationType().getSimpleName())
                .filter(name -> name.startsWith("Post") || name.startsWith("Put")
                        || name.startsWith("Patch") || name.startsWith("Delete"))
                .count();

        assertThat(writeMappings)
                .as("fee rules are reference data; no caller may create or change one")
                .isZero();
    }

    // -----------------------------------------------------------------
    // Fee rules must not leak into deterministic reconciliation
    // -----------------------------------------------------------------

    @Test
    void processorFeeIsStillNotADeterministicExceptionType() {
        assertThat(ExceptionType.values())
                .extracting(Enum::name)
                .as("a fee rule explains a difference; it does not reclassify one")
                .containsExactlyInAnyOrder(
                        "AMOUNT_MISMATCH", "MISSING_SETTLEMENT",
                        "DUPLICATE_SETTLEMENT", "CURRENCY_MISMATCH")
                .doesNotContain("PROCESSOR_FEE");
    }

    @Test
    void theDatabaseStillRefusesProcessorFeeAsAnExceptionType() {
        jdbc.update("INSERT INTO transactions (id, transaction_id, merchant_id, amount, "
                        + "expected_settlement_amount, currency, transaction_type, status, "
                        + "transaction_timestamp, created_at, updated_at) "
                        + "VALUES (?, 'TX-FEE-1', 'MERCHANT-104', 2500.00, 2500.00, 'USD', "
                        + "'PURCHASE', 'POSTED', ?, ?, ?)",
                UUID.randomUUID(), NOW, NOW, NOW);
        insertFeeRule("FR-80", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);

        assertThatThrownBy(() -> jdbc.update(
                "INSERT INTO reconciliation_exceptions (id, exception_id, transaction_id, "
                        + "exception_type, status, detected_at, created_at, updated_at) "
                        + "VALUES (?, 'EX-FEE-1', 'TX-FEE-1', 'PROCESSOR_FEE', 'OPEN', ?, ?, ?)",
                UUID.randomUUID(), NOW, NOW, NOW))
                .as("the presence of a matching fee rule changes nothing about detection")
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void aMatchingFeeRuleDoesNotChangeWhatReconciliationDetects() throws Exception {
        // A transaction expecting 2500.00 settled at 2450.00, and a fee rule for
        // exactly the 50.00 difference.
        String transactionId = createTransaction("2500.00");
        createSettlement(transactionId, "2450.00");
        insertFeeRule("FR-81", "MERCHANT-104", "NORTHSTAR_PAYMENTS", "PROCESSING", "50.00", "USD", true);
        entityManager.flush();

        mockMvc.perform(post("/api/v1/reconciliation/transactions/{id}", transactionId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.exceptions[0].exceptionType").value("AMOUNT_MISMATCH"))
                .andExpect(content().string(containsString("\"differenceAmount\":50.00")))
                .andExpect(content().string(org.hamcrest.Matchers.not(containsString("PROCESSOR_FEE"))));
    }

    // -----------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------

    private void insertFeeRule(String ruleId, String merchantId, String processor, String feeType,
                               String feeAmount, String currency, boolean active) {
        jdbc.update("INSERT INTO fee_rules (id, rule_id, merchant_id, processor, fee_type, "
                        + "fee_amount, currency, description, active, created_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), ruleId, merchantId, processor, feeType,
                new BigDecimal(feeAmount), currency, "Synthetic demo fee rule.", active, NOW);
    }

    private String createTransaction(String expectedSettlementAmount) throws Exception {
        String body = mockMvc.perform(post("/api/v1/transactions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "merchantId": "MERCHANT-104",
                                  "amount": %s,
                                  "expectedSettlementAmount": %s,
                                  "currency": "USD",
                                  "transactionType": "PURCHASE",
                                  "status": "POSTED",
                                  "transactionTimestamp": "2026-09-27T13:45:00Z"
                                }
                                """.formatted(expectedSettlementAmount, expectedSettlementAmount)))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        entityManager.flush();
        return com.jayway.jsonpath.JsonPath.read(body, "$.transactionId");
    }

    private void createSettlement(String transactionId, String settledAmount) throws Exception {
        mockMvc.perform(post("/api/v1/settlements")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "transactionId": "%s",
                                  "processor": "NORTHSTAR_PAYMENTS",
                                  "settledAmount": %s,
                                  "currency": "USD",
                                  "status": "COMPLETED",
                                  "settlementTimestamp": "2026-09-27T14:00:00Z"
                                }
                                """.formatted(transactionId, settledAmount)))
                .andExpect(status().isCreated());
        entityManager.flush();
    }
}
