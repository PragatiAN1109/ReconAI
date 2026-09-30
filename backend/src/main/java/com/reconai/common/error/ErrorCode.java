package com.reconai.common.error;

import org.springframework.http.HttpStatus;

/**
 * API error categories defined by docs/api-contract.md section 5.
 *
 * <p>The constant name is what appears in the {@code error} field of an error response,
 * so these names are part of the published contract.
 */
public enum ErrorCode {

    VALIDATION_ERROR(HttpStatus.BAD_REQUEST),
    NOT_FOUND(HttpStatus.NOT_FOUND),
    CONFLICT(HttpStatus.CONFLICT),
    INVALID_STATE_TRANSITION(HttpStatus.CONFLICT),
    INTERNAL_ERROR(HttpStatus.INTERNAL_SERVER_ERROR),

    /**
     * Declared by the API contract for failures of a downstream dependency. Nothing in
     * the deterministic financial core raises it: reconciliation has no dependency that
     * can be unavailable. It exists so the contract's categories are complete.
     */
    DEPENDENCY_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE),

    /**
     * The request body is larger than an endpoint accepts. Raised only by the public
     * demo endpoint, which bounds its input before reading it.
     */
    PAYLOAD_TOO_LARGE(HttpStatus.PAYLOAD_TOO_LARGE),

    /**
     * Too many requests in the current window. Raised only by the public demo
     * endpoint, which is reachable without authentication and so is throttled.
     */
    RATE_LIMITED(HttpStatus.TOO_MANY_REQUESTS);

    private final HttpStatus httpStatus;

    ErrorCode(HttpStatus httpStatus) {
        this.httpStatus = httpStatus;
    }

    public HttpStatus httpStatus() {
        return httpStatus;
    }
}
