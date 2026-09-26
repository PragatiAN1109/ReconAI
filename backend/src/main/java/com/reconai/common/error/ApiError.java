package com.reconai.common.error;

import java.time.Instant;

/**
 * The single error body shape returned by every endpoint, as specified in
 * docs/api-contract.md section 5.
 *
 * <p>It carries no stack trace, exception class name, SQL or database detail. The
 * {@code message} is always a sentence written for an API consumer.
 *
 * @param timestamp     when the error was produced, ISO-8601 UTC
 * @param status        HTTP status code
 * @param error         contract error category
 * @param message       human-readable description safe for external consumption
 * @param path          request path that produced the error
 * @param correlationId identifier tying this response to application logs
 */
public record ApiError(
        Instant timestamp,
        int status,
        ErrorCode error,
        String message,
        String path,
        String correlationId) {
}
