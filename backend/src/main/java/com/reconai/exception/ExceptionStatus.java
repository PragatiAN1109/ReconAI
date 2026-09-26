package com.reconai.exception;

/**
 * Lifecycle status of a reconciliation exception (docs/data-model.md sections 5, 19).
 *
 * <p>The documented flow is:
 *
 * <pre>
 * OPEN -> INVESTIGATING -> AWAITING_REVIEW -> RESOLVED | OPEN (rejected) | ESCALATED
 * </pre>
 *
 * <p>Only OPEN is reachable in the deterministic core: everything beyond it is driven
 * by investigation and human review, which are later phases. The later values exist so
 * the enum matches the documented lifecycle and the database check constraint.
 *
 * <p>Any status other than RESOLVED counts as unresolved for idempotency purposes; see
 * the partial unique index in V3__create_reconciliation_exceptions.sql.
 */
public enum ExceptionStatus {
    OPEN,
    INVESTIGATING,
    AWAITING_REVIEW,
    RESOLVED,
    ESCALATED
}
