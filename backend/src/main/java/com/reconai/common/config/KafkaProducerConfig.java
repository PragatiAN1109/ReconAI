package com.reconai.common.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.reconai.exception.ReconciliationExceptionEvent;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.StringSerializer;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.kafka.KafkaConnectionDetails;
import org.springframework.boot.autoconfigure.kafka.KafkaProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.core.ProducerFactory;
import org.springframework.kafka.support.serializer.JsonSerializer;

import java.util.Map;

/**
 * Producer wiring for reconciliation exception events.
 *
 * <p>The producer is built explicitly rather than left to auto-configuration for one
 * reason: Spring Kafka's {@link JsonSerializer} otherwise builds its own
 * {@link ObjectMapper}, which serialises an {@code Instant} as a numeric epoch value.
 * The event would then reach consumers as {@code "detectedAt": 1790000000.000000000}
 * while every REST response renders the same instant as
 * {@code "2026-09-26T14:32:00Z"} — two formats for one concept, and the message one is
 * awkward for a non-Java consumer to parse.
 *
 * <p>Injecting the application's own {@code ObjectMapper} makes the message format and
 * the API format identical, both governed by {@code spring.jackson} configuration.
 *
 * <p>Type information is also switched off. Left on, the producer attaches a
 * {@code __TypeId__} header naming the Java class, which leaks internal structure and
 * means nothing to a Python consumer.
 */
@Configuration
public class KafkaProducerConfig {

    @Bean
    public ProducerFactory<String, ReconciliationExceptionEvent> reconciliationExceptionProducerFactory(
            KafkaProperties kafkaProperties,
            ObjectProvider<KafkaConnectionDetails> connectionDetails,
            ObjectMapper objectMapper) {

        // max.block.ms and anything else under spring.kafka.producer still come from
        // configuration; only the serializers are fixed here.
        Map<String, Object> producerProperties = kafkaProperties.buildProducerProperties(null);

        // Defining the factory by hand means opting out of the auto-configuration that
        // normally applies connection details, so they are applied here instead. Without
        // this the broker address would always come from configuration, ignoring a
        // container-provided address in tests.
        connectionDetails.ifAvailable(details -> producerProperties.put(
                ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, details.getBootstrapServers()));

        JsonSerializer<ReconciliationExceptionEvent> valueSerializer =
                new JsonSerializer<>(objectMapper);
        valueSerializer.setAddTypeInfo(false);

        DefaultKafkaProducerFactory<String, ReconciliationExceptionEvent> producerFactory =
                new DefaultKafkaProducerFactory<>(producerProperties);
        producerFactory.setKeySerializer(new StringSerializer());
        producerFactory.setValueSerializer(valueSerializer);
        return producerFactory;
    }

    @Bean
    public KafkaTemplate<String, ReconciliationExceptionEvent> kafkaTemplate(
            ProducerFactory<String, ReconciliationExceptionEvent> reconciliationExceptionProducerFactory) {
        return new KafkaTemplate<>(reconciliationExceptionProducerFactory);
    }
}
