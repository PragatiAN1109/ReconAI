package com.reconai.common.error;

/** Raised when a request conflicts with the current state of a resource. */
public class ConflictException extends ApiException {

    public ConflictException(String message) {
        super(ErrorCode.CONFLICT, message);
    }
}
