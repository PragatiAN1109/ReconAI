package com.reconai.exception;

import com.reconai.common.error.NotFoundException;
import com.reconai.common.id.BusinessIdGenerator;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * Reads reconciliation exceptions and persists ones that a caller has already detected.
 *
 * <p>This service contains no reconciliation rules. It never loads settlements, never
 * compares amounts or currencies, and never works out which {@link ExceptionType}
 * applies. Detection belongs to the reconciliation engine; this class is the storage
 * and lookup it will use.
 */
@Service
public class ReconciliationExceptionService {

    private final ReconciliationExceptionRepository exceptionRepository;
    private final BusinessIdGenerator businessIdGenerator;
    private final ApplicationEventPublisher applicationEventPublisher;
    private final Clock clock;

    public ReconciliationExceptionService(ReconciliationExceptionRepository exceptionRepository,
                                          BusinessIdGenerator businessIdGenerator,
                                          ApplicationEventPublisher applicationEventPublisher,
                                          Clock clock) {
        this.exceptionRepository = exceptionRepository;
        this.businessIdGenerator = businessIdGenerator;
        this.applicationEventPublisher = applicationEventPublisher;
        this.clock = clock;
    }

    /**
     * Records a detected discrepancy, or returns the existing unresolved exception that
     * already describes it.
     *
     * <p>This is the idempotency guarantee reconciliation depends on. Running
     * reconciliation repeatedly over unchanged records returns the same exception every
     * time rather than accumulating EX-1042, EX-1043, EX-1044 for one problem. A
     * genuinely different discrepancy has a different natural key and does produce a new
     * exception, which is the intended behaviour.
     *
     * <p>Reuse is decided by {@code findUnresolvedEquivalent}, whose predicate matches
     * the database's partial unique index. The index remains the backstop if two
     * concurrent reconciliations race past the lookup.
     *
     * @return the reused or newly created exception; callers cannot tell which, and
     *         should not need to
     */
    @Transactional
    public ReconciliationException recordOrReuse(DetectedDiscrepancy discrepancy) {
        return findUnresolvedEquivalent(discrepancy)
                .orElseGet(() -> announceDetection(exceptionRepository.save(
                        ReconciliationException.detected(businessIdGenerator.nextExceptionId(),
                                discrepancy, Instant.now(clock)))));
    }

    /**
     * Announces a newly detected exception to the rest of the application.
     *
     * <p>Called only from the creation branch above. Reusing an existing unresolved
     * exception announces nothing, so calling the reconciliation endpoint repeatedly over
     * unchanged records cannot trigger repeated investigations of one discrepancy.
     *
     * <p>This is an in-process Spring event, not a message. It is raised inside the
     * transaction, and a listener forwards it onward only after that transaction commits,
     * so a rolled-back detection is never announced outside this process.
     */
    private ReconciliationException announceDetection(ReconciliationException created) {
        applicationEventPublisher.publishEvent(ReconciliationExceptionEvent.from(created));
        return created;
    }

    /**
     * Returns an unresolved exception already recording this discrepancy, if one exists.
     *
     * <p>Exposed separately from {@link #recordOrReuse} so the reconciliation engine can
     * ask whether a discrepancy is already known without creating anything.
     */
    @Transactional(readOnly = true)
    public Optional<ReconciliationException> findUnresolvedEquivalent(
            DetectedDiscrepancy discrepancy) {
        return exceptionRepository.findUnresolvedEquivalent(
                discrepancy.transactionId(),
                discrepancy.exceptionType(),
                discrepancy.settlementId(),
                discrepancy.expectedValue(),
                discrepancy.observedValue());
    }

    /**
     * Returns the exception with the given business identifier.
     *
     * @throws NotFoundException if no such exception exists
     */
    @Transactional(readOnly = true)
    public ReconciliationException getByExceptionId(String exceptionId) {
        return exceptionRepository.findByExceptionId(exceptionId)
                .orElseThrow(() -> new NotFoundException(
                        "Reconciliation exception " + exceptionId + " was not found."));
    }

    /**
     * Returns exceptions matching the supplied filters, newest detection first.
     *
     * <p>Any filter may be null, meaning it is not applied.
     */
    @Transactional(readOnly = true)
    public List<ReconciliationException> search(ExceptionStatus status,
                                                ExceptionType exceptionType,
                                                String transactionId) {
        return exceptionRepository.search(status, exceptionType, transactionId);
    }
}
