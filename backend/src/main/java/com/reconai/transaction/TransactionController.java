package com.reconai.transaction;

import com.reconai.transaction.dto.CreateTransactionRequest;
import com.reconai.transaction.dto.TransactionResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Transaction endpoints (docs/api-contract.md section 6).
 *
 * <p>The controller validates input, delegates, and maps the result to a response DTO.
 * It contains no business logic and never returns a JPA entity.
 */
@RestController
@RequestMapping("/api/v1/transactions")
public class TransactionController {

    private final TransactionService transactionService;

    public TransactionController(TransactionService transactionService) {
        this.transactionService = transactionService;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public TransactionResponse create(@Valid @RequestBody CreateTransactionRequest request) {
        return TransactionResponse.from(transactionService.create(request));
    }

    @GetMapping("/{transactionId}")
    public TransactionResponse getByTransactionId(@PathVariable String transactionId) {
        return TransactionResponse.from(transactionService.getByTransactionId(transactionId));
    }
}
