package com.reconai.exception;

import com.reconai.common.money.Money;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Locale;
import java.util.UUID;

/**
 * A discrepancy found between authoritative financial records
 * (docs/data-model.md section 5).
 *
 * <p>Despite the name this is a JPA entity, not a Java {@code Throwable}. It is a
 * persisted financial observation. Application errors live in
 * {@code com.reconai.common.error}.
 *
 * <p>The entity records WHAT disagrees and nothing more. It deliberately has no
 * rootCause, confidence, recommendation, evidence or investigation fields: those belong
 * to the investigation layer, which is a separate phase and a separate set of tables. An
 * exception saying "1247.50 was expected and 1217.50 was observed" is a fact. "A
 * processor fee caused it" is a conclusion, and conclusions are not stored here.
 */
@Entity
@Table(name = "reconciliation_exceptions")
public class ReconciliationException {

    /** Internal primary key. Never exposed through the API. */
    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /** Readable business identifier, for example {@code EX-1042}. Unique and immutable. */
    @Column(name = "exception_id", nullable = false, updatable = false, length = 50)
    private String exceptionId;

    @Column(name = "transaction_id", nullable = false, updatable = false, length = 50)
    private String transactionId;

    /**
     * The settlement this discrepancy concerns, when exactly one is involved.
     *
     * <p>Null for MISSING_SETTLEMENT, where there is no settlement, and for
     * DUPLICATE_SETTLEMENT, which concerns a set of settlements rather than one.
     */
    @Column(name = "settlement_id", updatable = false, length = 50)
    private String settlementId;

    @Enumerated(EnumType.STRING)
    @Column(name = "exception_type", nullable = false, updatable = false, length = 50)
    private ExceptionType exceptionType;

    /** What the authoritative transaction record led us to expect. */
    @Column(name = "expected_value", updatable = false, length = 255)
    private String expectedValue;

    /** What the authoritative settlement records actually showed. */
    @Column(name = "observed_value", updatable = false, length = 255)
    private String observedValue;

    /**
     * Monetary difference where one is meaningful, defined as
     * {@code expectedSettlementAmount - settledAmount}. Null for discrepancies that are
     * not about an amount.
     */
    @Column(name = "difference_amount", precision = 19, scale = 4, updatable = false)
    private BigDecimal differenceAmount;

    @Column(name = "currency", length = 3, updatable = false)
    private String currency;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 30)
    private ExceptionStatus status;

    @Column(name = "detected_at", nullable = false, updatable = false)
    private Instant detectedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    /** Required by JPA. */
    protected ReconciliationException() {
    }

    private ReconciliationException(UUID id, String exceptionId, DetectedDiscrepancy discrepancy,
                                    Instant detectedAt) {
        this.id = id;
        this.exceptionId = exceptionId;
        this.transactionId = discrepancy.transactionId();
        this.settlementId = discrepancy.settlementId();
        this.exceptionType = discrepancy.exceptionType();
        this.expectedValue = discrepancy.expectedValue();
        this.observedValue = discrepancy.observedValue();
        this.differenceAmount = Money.toStorageScale(discrepancy.differenceAmount());
        this.currency = discrepancy.currency() == null
                ? null
                : discrepancy.currency().toUpperCase(Locale.ROOT);
        // Newly detected discrepancies always start OPEN. Every later status is reached
        // through investigation or human review, neither of which exists yet.
        this.status = ExceptionStatus.OPEN;
        this.detectedAt = detectedAt;
        this.createdAt = detectedAt;
        this.updatedAt = detectedAt;
    }

    /**
     * Creates a newly detected exception in status OPEN.
     *
     * @param exceptionId business identifier allocated by {@code BusinessIdGenerator}
     * @param discrepancy what the caller observed; this class does not decide it
     * @param detectedAt  application clock reading for the detection timestamp
     */
    public static ReconciliationException detected(String exceptionId,
                                                   DetectedDiscrepancy discrepancy,
                                                   Instant detectedAt) {
        return new ReconciliationException(UUID.randomUUID(), exceptionId, discrepancy, detectedAt);
    }

    /** True when this exception still needs attention, matching the partial unique index. */
    public boolean isUnresolved() {
        return status != ExceptionStatus.RESOLVED;
    }

    public UUID getId() {
        return id;
    }

    public String getExceptionId() {
        return exceptionId;
    }

    public String getTransactionId() {
        return transactionId;
    }

    public String getSettlementId() {
        return settlementId;
    }

    public ExceptionType getExceptionType() {
        return exceptionType;
    }

    public String getExpectedValue() {
        return expectedValue;
    }

    public String getObservedValue() {
        return observedValue;
    }

    public BigDecimal getDifferenceAmount() {
        return differenceAmount;
    }

    public String getCurrency() {
        return currency;
    }

    public ExceptionStatus getStatus() {
        return status;
    }

    public Instant getDetectedAt() {
        return detectedAt;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
