package com.reconai.messaging;

import com.reconai.common.config.KafkaTopicProperties;
import com.reconai.exception.ExceptionType;
import com.reconai.exception.ReconciliationExceptionEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.kafka.core.KafkaTemplate;

import java.time.Instant;
import java.util.concurrent.CompletableFuture;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Unit tests for how the event reaches the broker: topic, key and payload. */
@ExtendWith(MockitoExtension.class)
class ReconciliationExceptionPublisherTest {

    private static final ReconciliationExceptionEvent EVENT = new ReconciliationExceptionEvent(
            "EX-1042", "TX-48291", ExceptionType.AMOUNT_MISMATCH,
            Instant.parse("2026-09-26T14:32:00Z"));

    @Mock
    private KafkaTemplate<String, ReconciliationExceptionEvent> kafkaTemplate;

    private ReconciliationExceptionPublisher publisher;

    @BeforeEach
    void setUp() {
        publisher = new ReconciliationExceptionPublisher(kafkaTemplate,
                new KafkaTopicProperties("reconciliation.exceptions"));
    }

    @Test
    void eventIsPublishedToTheConfiguredTopic() {
        when(kafkaTemplate.send(anyString(), anyString(), any()))
                .thenReturn(CompletableFuture.completedFuture(null));

        publisher.onReconciliationExceptionDetected(EVENT);

        ArgumentCaptor<String> topic = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(topic.capture(), anyString(), any());
        assertThat(topic.getValue()).isEqualTo("reconciliation.exceptions");
    }

    @Test
    void theTransactionIdIsUsedAsTheMessageKeySoEventsStayOrderedPerTransaction() {
        when(kafkaTemplate.send(anyString(), anyString(), any()))
                .thenReturn(CompletableFuture.completedFuture(null));

        publisher.onReconciliationExceptionDetected(EVENT);

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        verify(kafkaTemplate).send(anyString(), key.capture(), any());
        assertThat(key.getValue()).isEqualTo("TX-48291");
    }

    @Test
    void thePayloadIsTheTypedEventUnchanged() {
        when(kafkaTemplate.send(anyString(), anyString(), any()))
                .thenReturn(CompletableFuture.completedFuture(null));

        publisher.onReconciliationExceptionDetected(EVENT);

        ArgumentCaptor<ReconciliationExceptionEvent> payload =
                ArgumentCaptor.forClass(ReconciliationExceptionEvent.class);
        verify(kafkaTemplate).send(anyString(), anyString(), payload.capture());
        assertThat(payload.getValue()).isEqualTo(EVENT);
    }

    @Test
    void aBrokerFailureIsLoggedRatherThanThrownAtTheCaller() {
        when(kafkaTemplate.send(anyString(), anyString(), any()))
                .thenReturn(CompletableFuture.failedFuture(new IllegalStateException("broker down")));

        assertThatCode(() -> publisher.onReconciliationExceptionDetected(EVENT))
                .as("the financial exception is already committed; a publish failure "
                        + "must not propagate and must not invalidate it")
                .doesNotThrowAnyException();
    }

    @Test
    void publicationDoesNotWaitForBrokerAcknowledgement() {
        // A future that never completes stands in for a slow or unreachable broker.
        when(kafkaTemplate.send(anyString(), anyString(), any()))
                .thenReturn(new CompletableFuture<>());

        assertThatCode(() -> publisher.onReconciliationExceptionDetected(EVENT))
                .as("financial reconciliation must not wait for investigation delivery")
                .doesNotThrowAnyException();
    }
}
