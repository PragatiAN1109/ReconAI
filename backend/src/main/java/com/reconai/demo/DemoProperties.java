package com.reconai.demo;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Limits and server-forced values for the public demo endpoint.
 *
 * <p>Every value falls back to a sane default in the constructor, so the endpoint stays
 * bounded even if nothing is configured. Misconfiguring this to zero should not silently
 * remove a limit from a publicly reachable write endpoint.
 *
 * @param maxRequestBytes  largest request body accepted; a valid payload is a few hundred
 * @param perClientLimit   runs allowed per client address per window
 * @param globalLimit      runs allowed across all callers per window
 * @param windowSeconds    length of the fixed window
 * @param merchantId       merchant recorded on every demo transaction, never caller-supplied
 * @param processor        processor recorded on every demo settlement, never caller-supplied
 */
@ConfigurationProperties(prefix = "reconai.demo")
public record DemoProperties(
        int maxRequestBytes,
        int perClientLimit,
        int globalLimit,
        long windowSeconds,
        String merchantId,
        String processor) {

    public DemoProperties {
        if (maxRequestBytes <= 0) {
            maxRequestBytes = 4096;
        }
        if (perClientLimit <= 0) {
            perClientLimit = 10;
        }
        if (globalLimit <= 0) {
            globalLimit = 300;
        }
        if (windowSeconds <= 0) {
            windowSeconds = 60;
        }
        if (merchantId == null || merchantId.isBlank()) {
            merchantId = "DEMO-MERCHANT";
        }
        if (processor == null || processor.isBlank()) {
            processor = "DEMO-PROCESSOR";
        }
    }
}
