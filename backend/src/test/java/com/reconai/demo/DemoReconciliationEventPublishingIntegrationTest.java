package com.reconai.demo;

import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationExceptionEvent;
import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;
import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mockingDetails;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * What a demo run puts on Kafka, and what it deliberately does not do.
 *
 * <p>Not {@code @Transactional}: the publisher listens on {@code AFTER_COMMIT}, so a test
 * that rolls back proves nothing about publication. These transactions genuinely commit
 * and each test truncates afterwards, following
 * {@code ReconciliationEventPublishingIntegrationTest}.
 *
 * <p>The point of this class is the boundary. A demo run reaches Kafka by exactly the same
 * path as any other reconciliation — same publisher, same topic, same identity-only
 * payload — and it reaches nothing else. The financial core has no client for the
 * Investigation Service and no notion of a model, so a recruiter clicking "Run
 * Reconciliation" cannot cause an AI call. That is asserted here as far as this service
 * can: one event, carrying identity, and no other outbound interaction.
 */
@AutoConfigureMockMvc
class DemoReconciliationEventPublishingIntegrationTest extends PostgresIntegrationTest {

    private static final String PATH = "/api/v1/demo/reconcile";

    @MockitoBean(name = "kafkaTemplate")
    private KafkaTemplate<String, ReconciliationExceptionEvent> kafkaTemplate;

    private final MockMvc mockMvc;
    private final JdbcTemplate jdbc;

    @Autowired
    DemoReconciliationEventPublishingIntegrationTest(MockMvc mockMvc, JdbcTemplate jdbc) {
        this.mockMvc = mockMvc;
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

    @Test
    void aDemoExceptionPublishesExactlyOneIdentityOnlyEvent() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [{"settledAmount": "2450.00", "currency": "USD"}]
                        }"""))
                .andExpect(status().isCreated());

        assertThat(publishedEvents())
                .singleElement()
                .satisfies(event -> {
                    assertThat(event.type()).isEqualTo(ExceptionType.AMOUNT_MISMATCH);
                    assertThat(event.exceptionId()).startsWith("EX-");
                    assertThat(event.transactionId()).startsWith("TX-");
                    assertThat(event.detectedAt()).isNotNull();
                });
    }

    @Test
    void aMatchingDemoRunPublishesNothing() throws Exception {
        // No discrepancy means no exception, so there is nothing to investigate and
        // nothing is announced. This is the branch that must never reach the
        // investigation workflow.
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "2500.00", "expectedSettlementAmount": "2500.00", "currency": "USD"},
                          "settlements": [{"settledAmount": "2500.00", "currency": "USD"}]
                        }"""))
                .andExpect(status().isCreated());

        verify(kafkaTemplate, never()).send(anyString(), anyString(), any());
    }

    @Test
    void aRejectedDemoRunPublishesNothing() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "10.00", "expectedSettlementAmount": "10.00", "currency": "JPY"},
                          "settlements": []
                        }"""))
                .andExpect(status().isBadRequest());

        verify(kafkaTemplate, never()).send(anyString(), anyString(), any());
    }

    @Test
    void theEventIsKeyedByTransactionIdLikeEveryOtherExceptionEvent() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "100.00", "expectedSettlementAmount": "100.00", "currency": "USD"},
                          "settlements": []
                        }"""))
                .andExpect(status().isCreated());

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(anyString(), key.capture(), any());
        assertThat(key.getValue()).startsWith("TX-");
    }

    @Test
    void theDemoRunPublishesToTheConfiguredExceptionsTopicAndNoOther() throws Exception {
        mockMvc.perform(run("""
                        {
                          "transaction": {"amount": "100.00", "expectedSettlementAmount": "100.00", "currency": "USD"},
                          "settlements": []
                        }"""))
                .andExpect(status().isCreated());

        ArgumentCaptor<String> topic = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(topic.capture(), anyString(), any());
        assertThat(topic.getValue()).isEqualTo("reconciliation.exceptions");
    }

    private List<ReconciliationExceptionEvent> publishedEvents() {
        return mockingDetails(kafkaTemplate).getInvocations().stream()
                .filter(invocation -> "send".equals(invocation.getMethod().getName()))
                .map(invocation -> (ReconciliationExceptionEvent) invocation.getArgument(2))
                .toList();
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder run(
            String body) {
        return post(PATH).contentType(MediaType.APPLICATION_JSON).content(body);
    }
}
