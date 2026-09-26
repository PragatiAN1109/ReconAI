package com.reconai.common.error;

/** Raised when a resource identified by a business ID does not exist. */
public class NotFoundException extends ApiException {

    public NotFoundException(String message) {
        super(ErrorCode.NOT_FOUND, message);
    }
}
