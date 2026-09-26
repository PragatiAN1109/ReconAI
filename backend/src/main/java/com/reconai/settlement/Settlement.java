package com.reconai.settlement;

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
 * An authoritative record of what a processor reported settling
 * (docs/data-model.md section 4).
 *
 * <p>A settlement is ingested as a financial fact. Recording one says nothing about
 * whether it agrees with the transaction it references: deciding that is the
 * reconciliation engine's job, and it happens only when reconciliation is run.
 *
 * <p>The relationship to a transaction is {@code 1 ---- 0..*} and is held by business
 * identifier rather than a JPA association. Several settlements may reference the same
 * transaction, which is what makes duplicate settlement detection possible.
 */
@Entity
@Table(name = "settlements")
public class Settlement {

    /** Internal primary key. Never exposed through the API. */
    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /** Readable business identifier, for example {@code SET-8821}. Unique and immutable. */
    @Column(name = "settlement_id", nullable = false, updatable = false, length = 50)
    private String settlementId;

    /**
     * Business identifier of the related transaction. Deliberately not unique: a
     * transaction may have any number of settlements.
     */
    @Column(name = "transaction_id", nullable = false, updatable = false, length = 50)
    private String transactionId;

    @Column(name = "processor", nullable = false, length = 100)
    private String processor;

    @Column(name = "settled_amount", nullable = false, precision = 19, scale = 4)
    private BigDecimal settledAmount;

    /** ISO 4217 code, stored uppercase. */
    @Column(name = "currency", nullable = false, length = 3)
    private String currency;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 30)
    private SettlementStatus status;

    @Column(name = "settlement_timestamp", nullable = false)
    private Instant settlementTimestamp;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    /** Required by JPA. */
    protected Settlement() {
    }

    private Settlement(UUID id, String settlementId, String transactionId, String processor,
                       BigDecimal settledAmount, String currency, SettlementStatus status,
                       Instant settlementTimestamp, Instant createdAt) {
        this.id = id;
        this.settlementId = settlementId;
        this.transactionId = transactionId;
        this.processor = processor;
        // Normalised on the way in so a value written and a value later read back are
        // identical rather than differing only in scale.
        this.settledAmount = Money.toStorageScale(settledAmount);
        // Currency codes are compared during reconciliation. Normalising case here stops
        // "usd" and "USD" from being reported as a CURRENCY_MISMATCH.
        this.currency = currency.toUpperCase(Locale.ROOT);
        this.status = status;
        this.settlementTimestamp = settlementTimestamp;
        this.createdAt = createdAt;
    }

    /**
     * Creates a new settlement.
     *
     * @param settlementId business identifier allocated by {@code BusinessIdGenerator}
     * @param createdAt    application clock reading for the ingestion timestamp
     */
    public static Settlement create(String settlementId, String transactionId, String processor,
                                    BigDecimal settledAmount, String currency,
                                    SettlementStatus status, Instant settlementTimestamp,
                                    Instant createdAt) {
        return new Settlement(UUID.randomUUID(), settlementId, transactionId, processor,
                settledAmount, currency, status, settlementTimestamp, createdAt);
    }

    public UUID getId() {
        return id;
    }

    public String getSettlementId() {
        return settlementId;
    }

    public String getTransactionId() {
        return transactionId;
    }

    public String getProcessor() {
        return processor;
    }

    public BigDecimal getSettledAmount() {
        return settledAmount;
    }

    public String getCurrency() {
        return currency;
    }

    public SettlementStatus getStatus() {
        return status;
    }

    public Instant getSettlementTimestamp() {
        return settlementTimestamp;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
