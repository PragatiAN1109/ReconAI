package com.reconai.demo.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.util.List;

/**
 * One synthetic reconciliation run, submitted from the public Operations Console.
 *
 * <p>The shape mirrors the domain: a transaction has many settlements, so
 * {@code settlements} is a list rather than a single nullable object. An empty or absent
 * list is the natural expression of "nothing settled" and is what produces
 * {@code MISSING_SETTLEMENT} — no sentinel value and no null-versus-object ambiguity.
 *
 * <p>The list is capped well below anything interesting. Two entries already demonstrate
 * {@code DUPLICATE_SETTLEMENT}; the cap exists because this endpoint is public.
 */
public record DemoReconcileRequest(

        @NotNull(message = "transaction is required")
        @Valid
        DemoTransactionInput transaction,

        @Size(max = 3, message = "settlements must contain at most 3 entries")
        @Valid
        List<DemoSettlementInput> settlements) {

    /** The settlements to create, treating an absent list as an empty one. */
    public List<DemoSettlementInput> settlementsOrEmpty() {
        return settlements == null ? List.of() : settlements;
    }
}
