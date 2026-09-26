package com.reconai.settlement;

import com.reconai.settlement.dto.CreateSettlementRequest;
import com.reconai.settlement.dto.SettlementResponse;
import com.reconai.settlement.dto.TransactionSettlementsResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Settlement endpoints (docs/api-contract.md section 7).
 *
 * <p>There is no class-level base path because the two endpoints sit under different
 * resources: settlements are created against the settlement collection, and read as a
 * sub-resource of the transaction they belong to.
 */
@RestController
public class SettlementController {

    private final SettlementService settlementService;

    public SettlementController(SettlementService settlementService) {
        this.settlementService = settlementService;
    }

    @PostMapping("/api/v1/settlements")
    @ResponseStatus(HttpStatus.CREATED)
    public SettlementResponse create(@Valid @RequestBody CreateSettlementRequest request) {
        return SettlementResponse.from(settlementService.create(request));
    }

    @GetMapping("/api/v1/transactions/{transactionId}/settlements")
    public TransactionSettlementsResponse getForTransaction(@PathVariable String transactionId) {
        return TransactionSettlementsResponse.of(
                transactionId, settlementService.findByTransactionId(transactionId));
    }
}
