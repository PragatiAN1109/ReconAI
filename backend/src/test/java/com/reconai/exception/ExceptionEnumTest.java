package com.reconai.exception;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Guards the enum domains that the database check constraints mirror.
 *
 * <p>The PROCESSOR_FEE assertions are the first of three layers keeping detection and
 * investigation apart: it cannot be named in Java, cannot be parsed from a request, and
 * cannot be stored by PostgreSQL.
 */
class ExceptionEnumTest {

    @Test
    void exceptionTypeContainsExactlyTheFourDeterministicDiscrepancies() {
        assertThat(ExceptionType.values()).containsExactly(
                ExceptionType.AMOUNT_MISMATCH,
                ExceptionType.MISSING_SETTLEMENT,
                ExceptionType.DUPLICATE_SETTLEMENT,
                ExceptionType.CURRENCY_MISMATCH);
    }

    @Test
    void processorFeeCannotBeRepresentedAsAnExceptionType() {
        assertThat(ExceptionType.values())
                .extracting(Enum::name)
                .as("PROCESSOR_FEE is a root-cause classification, not a deterministic discrepancy")
                .doesNotContain("PROCESSOR_FEE");

        assertThatThrownBy(() -> ExceptionType.valueOf("PROCESSOR_FEE"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void exceptionStatusContainsExactlyTheDocumentedLifecycle() {
        assertThat(ExceptionStatus.values()).containsExactly(
                ExceptionStatus.OPEN,
                ExceptionStatus.INVESTIGATING,
                ExceptionStatus.AWAITING_REVIEW,
                ExceptionStatus.RESOLVED,
                ExceptionStatus.ESCALATED);
    }

    @Test
    void everyStatusExceptResolvedCountsAsUnresolvedForIdempotency() {
        assertThat(ExceptionStatus.values())
                .filteredOn(status -> status != ExceptionStatus.RESOLVED)
                .hasSize(4);
    }
}
