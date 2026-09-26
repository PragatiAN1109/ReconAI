package com.reconai.common.error;

/**
 * Base class for exceptions that map directly onto a contract error category.
 *
 * <p>The message of an {@code ApiException} is returned to the caller verbatim, so it
 * must never contain internal detail.
 */
public abstract class ApiException extends RuntimeException {

    private final ErrorCode errorCode;

    protected ApiException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public ErrorCode errorCode() {
        return errorCode;
    }
}
