package com.reconai.demo;

import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Transactional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.matchesPattern;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The public demo reconciliation endpoint, end to end against a real PostgreSQL.
 *
 * <p>Two things are being proved here. First, that the facade produces the same verdicts
 * the deterministic engine produces on its own — the four scenarios the console offers
 * plus the duplicate case the payload shape admits. Second, and more importantly, that it
 * is a facade and not a general-purpose write API: identity is server-supplied, input is
 * bounded, and no route here can amend or remove a record.
 */
@AutoConfigureMockMvc
@Transactional
class DemoReconciliationApiIntegrationTest extends PostgresIntegrationTest {

    private static final String PATH = "/api/v1/demo/reconcile";

    private final MockMvc mockMvc;
    private final JdbcTemplate jdbc;

    @Autowired
    DemoReconciliationApiIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc) {
        this.mockMvc = mockMvc;
        this.jdbc = jdbc;
    }

    // =================================================================
    // The four console scenarios
    // =================================================================

    @Test
    void matchingRecordsReconcileWithNoException() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [{"settledAmount": "2500.00", "currency": "USD"}]
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.transactionId", matchesPattern("^TX-\\d+$")))
                .andExpect(jsonPath("$.settlementIds", hasSize(1)))
                .andExpect(jsonPath("$.settlementIds[0]", matchesPattern("^SET-\\d+$")))
                .andExpect(jsonPath("$.reconciliation.reconciled").value(true))
                .andExpect(jsonPath("$.reconciliation.exceptions", hasSize(0)));

        assertThat(countExceptions()).isZero();
    }

    @Test
    void aSettlementForAnotherAmountIsReportedAsAmountMismatch() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [{"settledAmount": "2450.00", "currency": "USD"}]
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.reconciliation.reconciled").value(false))
                .andExpect(jsonPath("$.reconciliation.exceptions", hasSize(1)))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionType").value("AMOUNT_MISMATCH"))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionId", matchesPattern("^EX-\\d+$")))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].expectedValue").value("2500.00"))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].observedValue").value("2450.00"))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].currency").value("USD"))
                // Asserted on the raw body, as elsewhere: differenceAmount is a JSON
                // number, and this is what proves the trailing zeros survive
                // serialisation rather than arriving as 50.0.
                .andExpect(content().string(containsString("\"differenceAmount\":50.00")));
    }

    @Test
    void aSettlementInAnotherCurrencyIsReportedAsCurrencyMismatch() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [{"settledAmount": "2500.00", "currency": "EUR"}]
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionType").value("CURRENCY_MISMATCH"))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].expectedValue").value("USD"))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].observedValue").value("EUR"));
    }

    @Test
    void anEmptySettlementListIsReportedAsMissingSettlement() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": []
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.settlementIds", hasSize(0)))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionType").value("MISSING_SETTLEMENT"))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].observedValue").value("NO_SETTLEMENT"));
    }

    @Test
    void anAbsentSettlementListIsTreatedAsMissingSettlement() throws Exception {
        // The console always sends the key; a hand-edited payload may omit it entirely.
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "100.00", "expectedSettlementAmount": "100.00", "currency": "USD"}
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.settlementIds", hasSize(0)))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionType").value("MISSING_SETTLEMENT"));
    }

    // =================================================================
    // The payload shape admits duplicates, so the engine must still say so
    // =================================================================

    @Test
    void twoSettlementsAreReportedAsDuplicateSettlement() throws Exception {
        // Not offered as a console scenario in V1, but the 1:N payload allows it and the
        // deterministic precedence must be unchanged by the route taken to reach it.
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [
                            {"settledAmount": "2500.00", "currency": "USD"},
                            {"settledAmount": "2500.00", "currency": "USD"}
                          ]
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.settlementIds", hasSize(2)))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionType").value("DUPLICATE_SETTLEMENT"));
    }

    @Test
    void currencyMismatchStillTakesPrecedenceOverAnAmountDifference() throws Exception {
        // Precedence belongs to ReconciliationService and must not be re-decided here.
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [{"settledAmount": "9.99", "currency": "GBP"}]
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.reconciliation.exceptions", hasSize(1)))
                .andExpect(jsonPath("$.reconciliation.exceptions[0].exceptionType").value("CURRENCY_MISMATCH"));
    }

    // =================================================================
    // Identity is the server's, not the caller's
    // =================================================================

    @Test
    void callerSuppliedIdentityAndStatusFieldsAreIgnoredRatherThanHonoured() throws Exception {
        MvcResult result = mockMvc.perform(run("""
                        {
                          "transaction": {
                            "transactionId": "TX-HACKED",
                            "merchantId": "ATTACKER",
                            "status": "SETTLED",
                            "transactionType": "REFUND",
                            "transactionTimestamp": "1999-01-01T00:00:00Z",
                            "amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "USD"
                          },
                          "settlements": [{
                            "settlementId": "SET-HACKED",
                            "processor": "ATTACKER",
                            "status": "PENDING",
                            "settledAmount": "10.00", "currency": "USD"
                          }]
                        }"""))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.transactionId", matchesPattern("^TX-\\d+$")))
                .andExpect(jsonPath("$.settlementIds[0]", matchesPattern("^SET-\\d+$")))
                // PENDING was ignored: the settlement counted, so this reconciles cleanly.
                .andExpect(jsonPath("$.reconciliation.reconciled").value(true))
                .andReturn();

        String transactionId = com.jayway.jsonpath.JsonPath.read(
                result.getResponse().getContentAsString(), "$.transactionId");

        assertThat(jdbc.queryForObject(
                "SELECT merchant_id FROM transactions WHERE transaction_id = ?",
                String.class, transactionId)).isEqualTo("DEMO-MERCHANT");
        assertThat(jdbc.queryForObject(
                "SELECT status FROM transactions WHERE transaction_id = ?",
                String.class, transactionId)).isEqualTo("POSTED");
        assertThat(jdbc.queryForObject(
                "SELECT transaction_type FROM transactions WHERE transaction_id = ?",
                String.class, transactionId)).isEqualTo("PURCHASE");
        assertThat(jdbc.queryForObject(
                "SELECT processor FROM settlements WHERE transaction_id = ?",
                String.class, transactionId)).isEqualTo("DEMO-PROCESSOR");
        assertThat(jdbc.queryForObject(
                "SELECT status FROM settlements WHERE transaction_id = ?",
                String.class, transactionId)).isEqualTo("COMPLETED");
    }

    // =================================================================
    // Validation
    // =================================================================

    @Test
    void aMissingTransactionIsRejected() throws Exception {
        mockMvc.perform(run("""
                        {"settlements": []}"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message", containsString("transaction")));
    }

    @Test
    void malformedJsonIsRejectedAsAValidationErrorNotAServerError() throws Exception {
        mockMvc.perform(run("{ not json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void aCurrencyOutsideTheDemoAllowlistIsRejected() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "JPY"},
                          "settlements": []
                        }"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message", containsString("USD, EUR or GBP")));
    }

    @Test
    void anAmountAboveTheDemoCeilingIsRejected() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "9999999.00", "expectedSettlementAmount": "1.00", "currency": "USD"},
                          "settlements": []
                        }"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void aNegativeAmountIsRejected() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "-1.00", "expectedSettlementAmount": "-1.00", "currency": "USD"},
                          "settlements": []
                        }"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void moreThanTwoDecimalPlacesIsRejectedBeforeItCanReachMoneyRounding() throws Exception {
        // Money.toStorageScale rounds with UNNECESSARY and would throw on this.
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "10.00001", "expectedSettlementAmount": "10.00", "currency": "USD"},
                          "settlements": []
                        }"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void moreSettlementsThanTheListLimitIsRejected() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "USD"},
                          "settlements": [
                            {"settledAmount": "1.00", "currency": "USD"},
                            {"settledAmount": "1.00", "currency": "USD"},
                            {"settledAmount": "1.00", "currency": "USD"},
                            {"settledAmount": "1.00", "currency": "USD"}
                          ]
                        }"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.message", containsString("at most 3")));
    }

    @Test
    void aRejectedRequestPersistsNothing() throws Exception {
        long before = countTransactions();

        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "JPY"},
                          "settlements": []
                        }"""))
                .andExpect(status().isBadRequest());

        assertThat(countTransactions()).isEqualTo(before);
    }

    // =================================================================
    // The endpoint is not a general-purpose write API
    // =================================================================

    @Test
    void theDemoEndpointExposesNoReadUpdateOrDeleteOperation() throws Exception {
        mockMvc.perform(get(PATH)).andExpect(status().isMethodNotAllowed());
        mockMvc.perform(put(PATH).contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(patch(PATH).contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(delete(PATH)).andExpect(status().isMethodNotAllowed());
    }

    @Test
    void anOversizedBodyIsRefusedWithoutBeingParsed() throws Exception {
        String padding = "x".repeat(8192);
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "USD"},
                          "settlements": [],
                          "padding": "%s"
                        }""".formatted(padding)))
                .andExpect(status().isPayloadTooLarge())
                .andExpect(jsonPath("$.error").value("PAYLOAD_TOO_LARGE"))
                .andExpect(jsonPath("$.path").value(PATH));
    }

    // =================================================================
    // Helpers
    // =================================================================

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder run(String body) {
        return post(PATH).contentType(MediaType.APPLICATION_JSON).content(body);
    }

    private long countExceptions() {
        Long count = jdbc.queryForObject(
                "SELECT count(*) FROM reconciliation_exceptions", Long.class);
        return count == null ? 0 : count;
    }

    private long countTransactions() {
        Long count = jdbc.queryForObject("SELECT count(*) FROM transactions", Long.class);
        return count == null ? 0 : count;
    }
}
