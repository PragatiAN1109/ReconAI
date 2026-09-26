package com.reconai;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * ReconAI Financial Core.
 *
 * <p>This application is the authoritative system for transactions, settlements and
 * deterministic reconciliation. It contains no AI or LLM dependency, and must remain
 * fully functional when no investigation service exists.
 *
 * <p>It publishes detected discrepancies to Kafka so that investigation, whose latency
 * and failure modes are unrelated to financial correctness, can proceed independently.
 * Reconciliation never consumes from Kafka and never waits for investigation.
 */
@SpringBootApplication
@ConfigurationPropertiesScan
public class ReconAiApplication {

    public static void main(String[] args) {
        SpringApplication.run(ReconAiApplication.class, args);
    }
}
