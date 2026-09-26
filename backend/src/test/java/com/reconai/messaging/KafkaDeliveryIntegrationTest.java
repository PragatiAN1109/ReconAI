package com.reconai.messaging;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.reconai.reconciliation.ReconciliationService;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.kafka.KafkaContainer;
import org.testcontainers.utility.DockerImageName;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Properties;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Publishes through a real Kafka broker and reads the message back off the topic.
 *
 * <p>Isolated from every other test on purpose. The mocked tests prove <em>when</em> an
 * event is published; only a real broker proves <em>what a consumer actually receives</em>
 * — that {@code Instant} is serialised as ISO-8601 rather than an array of numbers, that
 * no Java type header is attached, and that the configured topic and key are the ones
 * used. Those are exactly the failures a mock cannot catch, and they would surface as a
 * broken Python consumer rather than as a failing build.
 *
 * <p>This class owns both of its containers rather than extending the shared Postgres
 * base, so the rest of the suite stays broker-free and unaffected.
 */
@SpringBootTest
@ActiveProfiles("test")
class KafkaDeliveryIntegrationTest {

    private static final String TOPIC = "reconciliation.exceptions";

    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>(DockerImageName.parse("postgres:16-alpine"));

    @ServiceConnection
    static final KafkaContainer KAFKA =
            new KafkaContainer(DockerImageName.parse("apache/kafka:3.8.1"));

    static {
        POSTGRES.start();
        KAFKA.start();
    }

    private final ReconciliationService reconciliationService;
    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Autowired
    KafkaDeliveryIntegrationTest(ReconciliationService reconciliationService, JdbcTemplate jdbc) {
        this.reconciliationService = reconciliationService;
        this.jdbc = jdbc;
    }

    @AfterEach
    void clearFinancialRecords() {
        jdbc.execute("TRUNCATE reconciliation_exceptions, settlements, transactions CASCADE");
    }

    @Test
    void aConsumerReceivesTheEventAsPlainJsonKeyedByTransactionId() throws Exception {
        String transactionId = transaction("1247.50", "USD");
        settlement(transactionId, "1217.50", "USD");

        try (KafkaConsumer<String, String> consumer = consumer()) {
            consumer.subscribe(List.of(TOPIC));
            consumer.poll(Duration.ofMillis(500));
            consumer.seekToBeginning(consumer.assignment());

            reconciliationService.reconcile(transactionId);

            ConsumerRecord<String, String> record = awaitRecord(consumer);

            assertThat(record.topic()).isEqualTo(TOPIC);
            assertThat(record.key())
                    .as("keyed by transaction so a consumer sees one transaction's events in order")
                    .isEqualTo(transactionId);

            JsonNode payload = objectMapper.readTree(record.value());

            assertThat(payload.fieldNames()).toIterable()
                    .as("exactly the four contract fields, nothing more")
                    .containsExactlyInAnyOrder("exceptionId", "transactionId", "type", "detectedAt");

            assertThat(payload.get("exceptionId").asText()).matches("^EX-\\d+$");
            assertThat(payload.get("transactionId").asText()).isEqualTo(transactionId);
            assertThat(payload.get("type").asText()).isEqualTo("AMOUNT_MISMATCH");

            assertThat(payload.get("detectedAt").isTextual())
                    .as("ISO-8601 text, not a numeric array a Python consumer could not parse")
                    .isTrue();
            assertThat(Instant.parse(payload.get("detectedAt").asText())).isNotNull();

            assertThat(record.headers().lastHeader("__TypeId__"))
                    .as("no Java class name is leaked to consumers")
                    .isNull();

            String raw = record.value();
            assertThat(raw)
                    .as("no internal UUID, settlement, amount, merchant or AI field on the wire")
                    .doesNotContain("settlementId", "settledAmount", "expectedValue",
                            "observedValue", "differenceAmount", "merchantId", "amount",
                            "rootCause", "confidence", "recommendation", "PROCESSOR_FEE");

            String internalId = jdbc.queryForObject(
                    "SELECT id::text FROM reconciliation_exceptions WHERE exception_id = ?",
                    String.class, payload.get("exceptionId").asText());
            assertThat(raw).doesNotContain(internalId);
        }
    }

    @Test
    void aSuccessfulReconciliationPutsNothingOnTheTopic() throws Exception {
        String transactionId = transaction("500.00", "USD");
        settlement(transactionId, "500.00", "USD");

        try (KafkaConsumer<String, String> consumer = consumer()) {
            consumer.subscribe(List.of(TOPIC));
            consumer.poll(Duration.ofMillis(500));
            consumer.seekToEnd(consumer.assignment());

            reconciliationService.reconcile(transactionId);

            ConsumerRecords<String, String> records = consumer.poll(Duration.ofSeconds(3));
            assertThat(records.isEmpty())
                    .as("records that agree are never announced for investigation")
                    .isTrue();
        }
    }

    // -----------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------

    private KafkaConsumer<String, String> consumer() {
        Properties properties = new Properties();
        properties.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KAFKA.getBootstrapServers());
        properties.put(ConsumerConfig.GROUP_ID_CONFIG, "test-" + UUID.randomUUID());
        properties.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        properties.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        properties.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        return new KafkaConsumer<>(properties);
    }

    private ConsumerRecord<String, String> awaitRecord(KafkaConsumer<String, String> consumer) {
        Instant deadline = Instant.now().plusSeconds(20);
        while (Instant.now().isBefore(deadline)) {
            ConsumerRecords<String, String> records = consumer.poll(Duration.ofMillis(500));
            for (ConsumerRecord<String, String> record : records) {
                return record;
            }
        }
        throw new AssertionError("No event arrived on " + TOPIC + " within 20 seconds");
    }

    private String transaction(String expectedSettlementAmount, String currency) {
        String transactionId = "TX-" + jdbc.queryForObject(
                "SELECT nextval('transaction_business_id_seq')", Long.class);
        jdbc.update("INSERT INTO transactions (id, transaction_id, merchant_id, amount, "
                        + "expected_settlement_amount, currency, transaction_type, status, "
                        + "transaction_timestamp, created_at, updated_at) "
                        + "VALUES (gen_random_uuid(), ?, 'MERCHANT-104', ?, ?, ?, 'PURCHASE', "
                        + "'POSTED', now(), now(), now())",
                transactionId, new BigDecimal(expectedSettlementAmount),
                new BigDecimal(expectedSettlementAmount), currency);
        return transactionId;
    }

    private void settlement(String transactionId, String settledAmount, String currency) {
        String settlementId = "SET-" + jdbc.queryForObject(
                "SELECT nextval('settlement_business_id_seq')", Long.class);
        jdbc.update("INSERT INTO settlements (id, settlement_id, transaction_id, processor, "
                        + "settled_amount, currency, status, settlement_timestamp, created_at) "
                        + "VALUES (gen_random_uuid(), ?, ?, 'NORTHSTAR_PAYMENTS', ?, ?, "
                        + "'COMPLETED', now(), now())",
                settlementId, transactionId, new BigDecimal(settledAmount), currency);
    }
}
