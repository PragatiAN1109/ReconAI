package com.reconai.common.error;

import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Tests the error contract itself rather than any one endpoint.
 *
 * <p>The category an error is given and the HTTP status it is returned with are related
 * but not interchangeable. The contract's categories are closed and do not cover every
 * status, so deriving the status from the category collapses distinct outcomes: a
 * request to a resource that exists but does not support the method is 405, and calling
 * it 400 tells the caller their request was malformed when it was not.
 *
 * <p>These tests use several unrelated endpoints on purpose. The behaviour belongs to
 * the handler, not to whichever controller happened to expose the gap.
 */
@AutoConfigureMockMvc
@Transactional
class GlobalExceptionHandlerIntegrationTest extends PostgresIntegrationTest {

    private final MockMvc mockMvc;

    @Autowired
    GlobalExceptionHandlerIntegrationTest(MockMvc mockMvc) {
        this.mockMvc = mockMvc;
    }

    // -----------------------------------------------------------------
    // An unsupported method is 405, not 400
    // -----------------------------------------------------------------

    @Test
    void anUnsupportedMethodOnAReadOnlyCollectionReturns405() throws Exception {
        mockMvc.perform(post("/api/v1/exceptions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isMethodNotAllowed());
    }

    @Test
    void everyUnsupportedMethodOnAReadOnlyCollectionReturns405() throws Exception {
        mockMvc.perform(put("/api/v1/exceptions")
                        .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(patch("/api/v1/exceptions")
                        .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isMethodNotAllowed());
        mockMvc.perform(delete("/api/v1/exceptions"))
                .andExpect(status().isMethodNotAllowed());
    }

    @Test
    void anUnsupportedMethodOnAWriteOnlyEndpointReturns405() throws Exception {
        // Reconciliation's batch endpoint is POST-only, so the mismatch runs the
        // other way round.
        mockMvc.perform(get("/api/v1/reconciliation/run"))
                .andExpect(status().isMethodNotAllowed());
    }

    @Test
    void aMethodNotAllowedResponseStillUsesTheContractErrorShape() throws Exception {
        mockMvc.perform(delete("/api/v1/exceptions"))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(jsonPath("$.status").value(405))
                .andExpect(jsonPath("$.error").exists())
                .andExpect(jsonPath("$.message").exists())
                .andExpect(jsonPath("$.path").value("/api/v1/exceptions"))
                .andExpect(jsonPath("$.timestamp").exists())
                .andExpect(jsonPath("$.correlationId").exists());
    }

    @Test
    void aMethodNotAllowedResponseLeaksNothingInternal() throws Exception {
        mockMvc.perform(delete("/api/v1/exceptions"))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(jsonPath("$.message").value(
                        org.hamcrest.Matchers.not(containsString("org.springframework"))))
                .andExpect(jsonPath("$.message").value(
                        org.hamcrest.Matchers.not(containsString("Exception"))));
    }

    @Test
    void theReportedStatusMatchesTheHttpStatus() throws Exception {
        // The body's status field and the response status must agree; they were
        // derived from the same place and must stay that way.
        mockMvc.perform(post("/api/v1/exceptions")
                        .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isMethodNotAllowed())
                .andExpect(jsonPath("$.status").value(405));

        mockMvc.perform(get("/api/v1/transactions/{id}", "TX-NOPE"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status").value(404));
    }

    // -----------------------------------------------------------------
    // Everything else is unchanged
    // -----------------------------------------------------------------

    @Test
    void aValidationFailureIsStill400() throws Exception {
        mockMvc.perform(post("/api/v1/transactions")
                        .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void aMalformedBodyIsStill400() throws Exception {
        mockMvc.perform(post("/api/v1/transactions")
                        .contentType(MediaType.APPLICATION_JSON).content("{ not json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.message").value("Request body is missing or malformed."));
    }

    @Test
    void anUnknownEnumFilterValueIsStill400() throws Exception {
        mockMvc.perform(get("/api/v1/exceptions").param("status", "NOT_A_STATUS"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("VALIDATION_ERROR"));
    }

    @Test
    void anUnknownResourceIsStill404() throws Exception {
        mockMvc.perform(get("/api/v1/transactions/{id}", "TX-48291"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.message").value("Transaction TX-48291 was not found."));
    }

    @Test
    void anUnknownExceptionIsStill404() throws Exception {
        mockMvc.perform(get("/api/v1/exceptions/{id}", "EX-99999"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("NOT_FOUND"));
    }

    @Test
    void theCorrelationIdIsStillEchoedOnErrors() throws Exception {
        mockMvc.perform(get("/api/v1/transactions/{id}", "TX-48291")
                        .header("X-Correlation-ID", "CORR-89123"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.correlationId").value("CORR-89123"));
    }
}
