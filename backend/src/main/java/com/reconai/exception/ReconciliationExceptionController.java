package com.reconai.exception;

import com.reconai.exception.dto.ExceptionListResponse;
import com.reconai.exception.dto.ExceptionResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Reconciliation exception endpoints (docs/api-contract.md section 9).
 *
 * <p>Read-only. Exceptions are created by the reconciliation engine, never by an API
 * caller: a discrepancy is something the system observes between authoritative records,
 * not something a client can assert.
 */
@RestController
@RequestMapping("/api/v1/exceptions")
public class ReconciliationExceptionController {

    private final ReconciliationExceptionService exceptionService;

    public ReconciliationExceptionController(ReconciliationExceptionService exceptionService) {
        this.exceptionService = exceptionService;
    }

    /**
     * Lists exceptions, optionally filtered. Omitted parameters are not applied, so the
     * three filters combine freely.
     */
    @GetMapping
    public ExceptionListResponse list(
            @RequestParam(required = false) ExceptionStatus status,
            @RequestParam(required = false) ExceptionType exceptionType,
            @RequestParam(required = false) String transactionId) {
        return ExceptionListResponse.of(
                exceptionService.search(status, exceptionType, transactionId));
    }

    @GetMapping("/{exceptionId}")
    public ExceptionResponse getByExceptionId(@PathVariable String exceptionId) {
        return ExceptionResponse.from(exceptionService.getByExceptionId(exceptionId));
    }
}
