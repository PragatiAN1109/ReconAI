package com.reconai.exception;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

/** Persistence access for reconciliation exceptions. */
public interface ReconciliationExceptionRepository
        extends JpaRepository<ReconciliationException, UUID> {

    Optional<ReconciliationException> findByExceptionId(String exceptionId);

    /**
     * Returns exceptions matching whichever filters were supplied.
     *
     * <p>A null argument means "do not filter on this", which keeps the three optional
     * query parameters combinable without a specification or criteria builder.
     *
     * <p>Ordering is newest detection first with the exception ID breaking ties, so two
     * identical requests always return the same sequence.
     */
    @Query("""
            SELECT e FROM ReconciliationException e
            WHERE (:status IS NULL OR e.status = :status)
              AND (:exceptionType IS NULL OR e.exceptionType = :exceptionType)
              AND (:transactionId IS NULL OR e.transactionId = :transactionId)
            ORDER BY e.detectedAt DESC, e.exceptionId ASC
            """)
    List<ReconciliationException> search(@Param("status") ExceptionStatus status,
                                         @Param("exceptionType") ExceptionType exceptionType,
                                         @Param("transactionId") String transactionId);

    /**
     * Finds an unresolved exception already recording the same discrepancy.
     *
     * <p>This is the query behind reconciliation idempotency. Its predicate mirrors the
     * partial unique index {@code ux_reconciliation_exceptions_unresolved_natural_key}
     * exactly, including the COALESCE over nullable columns, so the application check
     * and the database constraint can never disagree about what counts as equivalent.
     *
     * <p>RESOLVED exceptions are excluded on purpose: a discrepancy that was settled and
     * then recurs is a new discrepancy and deserves a new record.
     */
    @Query("""
            SELECT e FROM ReconciliationException e
            WHERE e.transactionId = :transactionId
              AND e.exceptionType = :exceptionType
              AND e.status <> com.reconai.exception.ExceptionStatus.RESOLVED
              AND COALESCE(e.settlementId, '') = COALESCE(:settlementId, '')
              AND COALESCE(e.expectedValue, '') = COALESCE(:expectedValue, '')
              AND COALESCE(e.observedValue, '') = COALESCE(:observedValue, '')
            """)
    Optional<ReconciliationException> findUnresolvedEquivalent(
            @Param("transactionId") String transactionId,
            @Param("exceptionType") ExceptionType exceptionType,
            @Param("settlementId") String settlementId,
            @Param("expectedValue") String expectedValue,
            @Param("observedValue") String observedValue);
}
