package com.reconai.exception.dto;

import com.reconai.exception.ReconciliationException;

import java.util.List;

/**
 * Response body for {@code GET /api/v1/exceptions} (docs/api-contract.md section 9.1).
 *
 * <p>{@code total} is the number of items returned. V1 has no pagination, so it is not
 * a count of some larger result set.
 */
public record ExceptionListResponse(List<ExceptionResponse> items, int total) {

    public static ExceptionListResponse of(List<ReconciliationException> exceptions) {
        List<ExceptionResponse> items = exceptions.stream().map(ExceptionResponse::from).toList();
        return new ExceptionListResponse(items, items.size());
    }
}
