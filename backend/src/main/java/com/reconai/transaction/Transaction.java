package com.reconai.transaction;

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
 * An authoritative internal record of what the financial system expects to settle
 * (docs/data-model.md section 3).
 *
 * <p>A transaction is a financial fact. It is never created or modified by AI output,
 * and reconciliation only ever reads it.
 *
 * <p>The entity is never returned from a controller; the API exposes
 * {@code TransactionResponse} instead, which keeps the internal UUID private.
 */
@Entity
@Table(name = "transactions")
public class Transaction {

    /** Internal primary key. Never exposed through the API. */
    @Id
    @Column(name = "id", nullable = false, updatable = false)
    private UUID id;

    /** Readable business identifier, for example {@code TX-48291}. Unique and immutable. */
    @Column(name = "transaction_id", nullable = false, updatable = false, length = 50)
    private String transactionId;

    @Column(name = "merchant_id", nullable = false, length = 50)
    private String merchantId;

    @Column(name = "amount", nullable = false, precision = 19, scale = 4)
    private BigDecimal amount;

    @Column(name = "expected_settlement_amount", nullable = false, precision = 19, scale = 4)
    private BigDecimal expectedSettlementAmount;

    /** ISO 4217 code, stored uppercase. Also the currency the settlement is expected in. */
    @Column(name = "currency", nullable = false, length = 3)
    private String currency;

    @Enumerated(EnumType.STRING)
    @Column(name = "transaction_type", nullable = false, length = 30)
    private TransactionType transactionType;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 30)
    private TransactionStatus status;

    @Column(name = "transaction_timestamp", nullable = false)
    private Instant transactionTimestamp;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    /** Required by JPA. */
    protected Transaction() {
    }

    private Transaction(UUID id, String transactionId, String merchantId, BigDecimal amount,
                        BigDecimal expectedSettlementAmount, String currency,
                        TransactionType transactionType, TransactionStatus status,
                        Instant transactionTimestamp, Instant createdAt) {
        this.id = id;
        this.transactionId = transactionId;
        this.merchantId = merchantId;
        // Normalised on the way in so that a value written and a value later read back
        // are identical, rather than differing only in scale.
        this.amount = Money.toStorageScale(amount);
        this.expectedSettlementAmount = Money.toStorageScale(expectedSettlementAmount);
        // Currency codes are compared during reconciliation. Normalising case here stops
        // "usd" and "USD" from being reported as a CURRENCY_MISMATCH.
        this.currency = currency.toUpperCase(Locale.ROOT);
        this.transactionType = transactionType;
        this.status = status;
        this.transactionTimestamp = transactionTimestamp;
        this.createdAt = createdAt;
        this.updatedAt = createdAt;
    }

    /**
     * Creates a new transaction.
     *
     * @param transactionId business identifier allocated by {@code BusinessIdGenerator}
     * @param createdAt     application clock reading used for both audit timestamps
     */
    public static Transaction create(String transactionId, String merchantId, BigDecimal amount,
                                     BigDecimal expectedSettlementAmount, String currency,
                                     TransactionType transactionType, TransactionStatus status,
                                     Instant transactionTimestamp, Instant createdAt) {
        return new Transaction(UUID.randomUUID(), transactionId, merchantId, amount,
                expectedSettlementAmount, currency, transactionType, status,
                transactionTimestamp, createdAt);
    }

    public UUID getId() {
        return id;
    }

    public String getTransactionId() {
        return transactionId;
    }

    public String getMerchantId() {
        return merchantId;
    }

    public BigDecimal getAmount() {
        return amount;
    }

    public BigDecimal getExpectedSettlementAmount() {
        return expectedSettlementAmount;
    }

    public String getCurrency() {
        return currency;
    }

    public TransactionType getTransactionType() {
        return transactionType;
    }

    public TransactionStatus getStatus() {
        return status;
    }

    public Instant getTransactionTimestamp() {
        return transactionTimestamp;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
