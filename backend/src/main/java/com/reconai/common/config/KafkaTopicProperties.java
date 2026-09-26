package com.reconai.common.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Topic names the financial core publishes to.
 *
 * <p>Kept in configuration rather than in code so that the same artifact runs against a
 * local broker, a compose stack and a managed cluster without rebuilding.
 *
 * @param reconciliationExceptionsTopic topic carrying newly detected discrepancies
 */
@ConfigurationProperties(prefix = "reconai.kafka")
public record KafkaTopicProperties(String reconciliationExceptionsTopic) {
}
