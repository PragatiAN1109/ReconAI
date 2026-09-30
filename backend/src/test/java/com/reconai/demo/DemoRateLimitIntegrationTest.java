package com.reconai.demo;

import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Throttling of the public demo endpoint, with the limits turned down.
 *
 * <p>Its own class because the limits have to be lowered for the whole context.
 *
 * <p>The limiter is one object per context and the window is set long enough that it
 * cannot roll mid-class, so every test here uses a distinct client address and the global
 * limit is left effectively unlimited. Sharing a small global budget across test methods
 * would make each result depend on the order the others happened to run in — the global
 * limit is therefore proved in {@link DemoGlobalRateLimitIntegrationTest}, which has one
 * test and its own context. Window rolling is covered by
 * {@link FixedWindowRateLimiterTest}, which controls the clock.
 */
@AutoConfigureMockMvc
@Transactional
@TestPropertySource(properties = {
        "reconai.demo.per-client-limit=2",
        "reconai.demo.global-limit=100000",
        "reconai.demo.window-seconds=600"
})
class DemoRateLimitIntegrationTest extends PostgresIntegrationTest {

    private static final String PATH = "/api/v1/demo/reconcile";

    private static final String VALID_BODY = """
            {
              "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "USD"},
              "settlements": [{"settledAmount": "10.00", "currency": "USD"}]
            }""";

    private final MockMvc mockMvc;

    @Autowired
    DemoRateLimitIntegrationTest(MockMvc mockMvc) {
        this.mockMvc = mockMvc;
    }

    @Test
    void aClientPastItsAllowanceIsRefusedWithTheApiErrorEnvelope() throws Exception {
        mockMvc.perform(run("1.1.1.1")).andExpect(status().isCreated());
        mockMvc.perform(run("1.1.1.1")).andExpect(status().isCreated());

        mockMvc.perform(run("1.1.1.1"))
                .andExpect(status().isTooManyRequests())
                .andExpect(jsonPath("$.error").value("RATE_LIMITED"))
                .andExpect(jsonPath("$.status").value(429))
                .andExpect(jsonPath("$.path").value(PATH))
                .andExpect(jsonPath("$.correlationId").exists())
                .andExpect(header().exists("Retry-After"));
    }

    @Test
    void oneClientExhaustingItsAllowanceDoesNotBlockAnother() throws Exception {
        mockMvc.perform(run("2.2.2.2")).andExpect(status().isCreated());
        mockMvc.perform(run("2.2.2.2")).andExpect(status().isCreated());
        mockMvc.perform(run("2.2.2.2")).andExpect(status().isTooManyRequests());

        mockMvc.perform(run("3.3.3.3")).andExpect(status().isCreated());
    }

    @Test
    void readEndpointsAreNeverThrottled() throws Exception {
        // Only the one public write route is limited. Browsing is not rationed.
        for (int attempt = 0; attempt < 6; attempt++) {
            mockMvc.perform(get("/api/v1/exceptions").header("X-Forwarded-For", "5.5.5.5"))
                    .andExpect(status().isOk());
        }
    }

    @Test
    void theGenericWriteEndpointsAreNotThrottledBecauseTheyAreNotPublic() throws Exception {
        // The filter is scoped to the demo path. These are unreachable from the
        // internet by CloudFront method restriction, not by throttling, so they must
        // behave exactly as before — a validation failure here, never a 429.
        for (int attempt = 0; attempt < 6; attempt++) {
            mockMvc.perform(post("/api/v1/transactions")
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{}")
                            .header("X-Forwarded-For", "6.6.6.6"))
                    .andExpect(status().isBadRequest());
        }
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder run(
            String clientAddress) {
        return post(PATH)
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_BODY)
                .header("X-Forwarded-For", clientAddress);
    }
}
