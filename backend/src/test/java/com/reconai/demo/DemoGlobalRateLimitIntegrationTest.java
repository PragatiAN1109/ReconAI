package com.reconai.demo;

import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * The overall allowance for the demo endpoint, independent of any one caller.
 *
 * <p>Exactly one test, in its own context, holding the only small global budget in the
 * suite. The limiter is a singleton for the life of a context, so a second test here
 * would either start with the budget already spent or force the budget high enough that
 * this one could no longer reach it.
 *
 * <p>This is the limit that matters: per-client throttling stops one visitor
 * monopolising the demo, but a global ceiling is the only thing that bounds total load
 * from an arbitrary number of callers.
 */
@AutoConfigureMockMvc
@Transactional
@TestPropertySource(properties = {
        "reconai.demo.per-client-limit=100",
        "reconai.demo.global-limit=3",
        "reconai.demo.window-seconds=600"
})
class DemoGlobalRateLimitIntegrationTest extends PostgresIntegrationTest {

    private static final String PATH = "/api/v1/demo/reconcile";

    private static final String VALID_BODY = """
            {
              "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "USD"},
              "settlements": [{"settledAmount": "10.00", "currency": "USD"}]
            }""";

    private final MockMvc mockMvc;

    @Autowired
    DemoGlobalRateLimitIntegrationTest(MockMvc mockMvc) {
        this.mockMvc = mockMvc;
    }

    @Test
    void theGlobalAllowanceRefusesAFreshClientThatHasUsedNothing() throws Exception {
        // Four distinct callers, each far inside its own allowance of 100. The
        // endpoint's overall allowance is three, so the fourth is refused even
        // though nothing about that caller is excessive.
        mockMvc.perform(run("4.0.0.1")).andExpect(status().isCreated());
        mockMvc.perform(run("4.0.0.2")).andExpect(status().isCreated());
        mockMvc.perform(run("4.0.0.3")).andExpect(status().isCreated());

        mockMvc.perform(run("4.0.0.4"))
                .andExpect(status().isTooManyRequests())
                .andExpect(jsonPath("$.error").value("RATE_LIMITED"))
                // The message names the real reason, so a visitor is not told to
                // slow down when the demo as a whole is simply out of allowance.
                .andExpect(jsonPath("$.message", containsString("overall request limit")));
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder run(
            String clientAddress) {
        return post(PATH)
                .contentType(MediaType.APPLICATION_JSON)
                .content(VALID_BODY)
                .header("X-Forwarded-For", clientAddress);
    }
}
