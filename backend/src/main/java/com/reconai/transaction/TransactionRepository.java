package com.reconai.transaction;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

/**
 * Persistence access for transactions.
 *
 * <p>Lookups are by business identifier: it is the value the API, other tables and the
 * reconciliation engine all use. The UUID primary key is an implementation detail.
 */
public interface TransactionRepository extends JpaRepository<Transaction, UUID> {

    Optional<Transaction> findByTransactionId(String transactionId);
}
