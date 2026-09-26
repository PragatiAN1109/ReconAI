package com.reconai.common.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;

/**
 * Supplies the clock used for all application-generated timestamps.
 *
 * <p>Timestamps are injected rather than read from {@code Instant.now()} so that
 * reconciliation, which must be deterministic, has no hidden dependency on wall-clock
 * time and can be driven by a fixed clock in tests.
 */
@Configuration
public class ClockConfig {

    @Bean
    public Clock clock() {
        return Clock.systemUTC();
    }
}
