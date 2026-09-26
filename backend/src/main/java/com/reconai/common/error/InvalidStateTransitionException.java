package com.reconai.common.error;

/**
 * Raised when a request would move a resource into a state its lifecycle does not
 * permit, for example resolving an exception that was never opened.
 */
public class InvalidStateTransitionException extends ApiException {

    public InvalidStateTransitionException(String message) {
        super(ErrorCode.INVALID_STATE_TRANSITION, message);
    }
}
