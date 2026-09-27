package com.reconai.feerule;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * Structured fee configuration for a processor, optionally narrowed to one merchant
 * (docs/data-model.md section 8).
 *
 * <p>A fee rule is reference data and <strong>evidence</strong>. It states that a fee of
 * this shape applies to this processor. It does not state that any particular
 * transaction was charged one — establishing that needs the transaction, its
 * settlements and a judgement, none of which live here.
 *
 * <p>Nothing in the financial core reads this table during reconciliation. Deterministic
 * reconciliation compares authoritative records and nothing else; a $50 difference is an
 * {@code AMOUNT_MISMATCH} whether or not a $50 fee rule happens to exist.
 */
@Entity
@Table(name = "fee_rules")
public class FeeRule {

    /** Internal primary key. Never exposed through the API. */
    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /** Readable business identifier, for example {@code FR-14}. Unique and immutable. */
    @Column(name = "rule_id", nullable = false, updatable = false, length = 50)
    private String ruleId;

    /** Null when the rule applies to every merchant on this processor. */
    @Column(name = "merchant_id", length = 50)
    private String merchantId;

    @Column(name = "processor", nullable = false, length = 100)
    private String processor;

    @Enumerated(EnumType.STRING)
    @Column(name = "fee_type", nullable = false, length = 50)
    private FeeType feeType;

    @Column(name = "fee_amount", nullable = false, precision = 19, scale = 4)
    private BigDecimal feeAmount;

    @Column(name = "currency", nullable = false, length = 3)
    private String currency;

    @Column(name = "description", nullable = false, columnDefinition = "text")
    private String description;

    @Column(name = "active", nullable = false)
    private boolean active;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    /** Required by JPA. */
    protected FeeRule() {
    }

    public UUID getId() {
        return id;
    }

    public String getRuleId() {
        return ruleId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public String getProcessor() {
        return processor;
    }

    public FeeType getFeeType() {
        return feeType;
    }

    public BigDecimal getFeeAmount() {
        return feeAmount;
    }

    public String getCurrency() {
        return currency;
    }

    public String getDescription() {
        return description;
    }

    public boolean isActive() {
        return active;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
