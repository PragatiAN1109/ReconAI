package com.reconai.messaging;

import com.reconai.common.config.CorrelationIdFilter;
import com.reconai.common.config.KafkaTopicProperties;
import com.reconai.exception.ReconciliationExceptionEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * Forwards newly detected reconciliation exceptions to Kafka.
 *
 * <p>This is the only class in the financial core that touches messaging. Reconciliation
 * itself has no idea Kafka exists, which is what keeps the deterministic engine testable
 * and independently correct.
 *
 * <h2>Why publication happens after commit</h2>
 *
 * The listener runs in {@link TransactionPhase#AFTER_COMMIT}. Spring buffers the
 * in-process event on the transaction synchronization and releases it only once the
 * database transaction has committed, so an investigation is never triggered for an
 * exception that rolled back. {@code fallbackExecution} is left at its default of false:
 * with no transaction in progress there is nothing to commit, and nothing is published.
 *
 * <h2>Why nothing waits for the broker</h2>
 *
 * The send future is never awaited. Producer-side synchronous blocking is bounded using
 * {@code max.block.ms}, while send completion or failure is handled asynchronously for
 * logging. The guarantee being protected is that financial reconciliation does not wait
 * for AI investigation.
 *
 * <h2>Delivery limitation</h2>
 *
 * A database commit and a Kafka publication are not atomic. If the commit succeeds and
 * publication then fails, the financial exception remains correctly persisted, the
 * failure is logged, and V1 may lose the investigation trigger — the discrepancy is
 * still visible through the exception APIs and can be re-detected. A Kafka failure must
 * never roll back or invalidate an already committed financial record. A transactional
 * outbox is the production hardening path; it is deliberately not built here.
 */
@Component
public class ReconciliationExceptionPublisher {

    private static final Logger log =
            LoggerFactory.getLogger(ReconciliationExceptionPublisher.class);

    private final KafkaTemplate<String, ReconciliationExceptionEvent> kafkaTemplate;
    private final KafkaTopicProperties topicProperties;

    public ReconciliationExceptionPublisher(
            KafkaTemplate<String, ReconciliationExceptionEvent> kafkaTemplate,
            KafkaTopicProperties topicProperties) {
        this.kafkaTemplate = kafkaTemplate;
        this.topicProperties = topicProperties;
    }

    /**
     * Publishes one event per newly created exception, after the transaction commits.
     *
     * <p>The transaction ID is the message key, so every event about one transaction
     * lands on the same partition and reaches a consumer in order.
     */
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT, fallbackExecution = false)
    public void onReconciliationExceptionDetected(ReconciliationExceptionEvent event) {
        String topic = topicProperties.reconciliationExceptionsTopic();

        // Captured before the send: the completion callback may run on a producer I/O
        // thread, where the request-scoped logging context is no longer present.
        String correlationId = MDC.get(CorrelationIdFilter.MDC_KEY);

        kafkaTemplate.send(topic, event.transactionId(), event)
                .whenComplete((result, failure) -> {
                    if (failure == null) {
                        log.info("Published reconciliation exception event "
                                        + "[topic={} key={} exceptionId={} type={} correlationId={}]",
                                topic, event.transactionId(), event.exceptionId(), event.type(),
                                correlationId);
                    } else {
                        // The financial record is already committed and stays valid. Only
                        // the investigation trigger is lost.
                        log.error("Failed to publish reconciliation exception event; the "
                                        + "exception remains persisted and readable "
                                        + "[topic={} key={} exceptionId={} correlationId={}]",
                                topic, event.transactionId(), event.exceptionId(), correlationId,
                                failure);
                    }
                });
    }
}
