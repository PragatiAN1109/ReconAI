package com.reconai.common.id;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Allocates the readable business identifiers used throughout the API, logs and
 * demonstrations (docs/data-model.md section 2.2).
 *
 * <p>Identifiers come from PostgreSQL sequences created by the Flyway migrations. A
 * sequence guarantees uniqueness without a read-then-write race or a retry loop, and
 * because sequence allocation is not transactional a rolled-back request simply leaves
 * a gap in the numbering rather than risking a reused identifier.
 *
 * <p>Business IDs are always distinct from the internal UUID primary key. The UUID is
 * never exposed by the API; the business ID is what callers and other tables reference.
 */
@Component
public class BusinessIdGenerator {

    private static final String TRANSACTION_PREFIX = "TX-";
    private static final String TRANSACTION_SEQUENCE = "transaction_business_id_seq";

    private static final String SETTLEMENT_PREFIX = "SET-";
    private static final String SETTLEMENT_SEQUENCE = "settlement_business_id_seq";

    private static final String EXCEPTION_PREFIX = "EX-";
    private static final String EXCEPTION_SEQUENCE = "exception_business_id_seq";

    private final JdbcTemplate jdbcTemplate;

    public BusinessIdGenerator(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** Returns the next transaction identifier, for example {@code TX-10001}. */
    public String nextTransactionId() {
        return TRANSACTION_PREFIX + nextValue(TRANSACTION_SEQUENCE);
    }

    /** Returns the next settlement identifier, for example {@code SET-8001}. */
    public String nextSettlementId() {
        return SETTLEMENT_PREFIX + nextValue(SETTLEMENT_SEQUENCE);
    }

    /** Returns the next reconciliation exception identifier, for example {@code EX-1001}. */
    public String nextExceptionId() {
        return EXCEPTION_PREFIX + nextValue(EXCEPTION_SEQUENCE);
    }

    private long nextValue(String sequenceName) {
        Long value = jdbcTemplate.queryForObject("SELECT nextval('" + sequenceName + "')", Long.class);
        if (value == null) {
            throw new IllegalStateException("Sequence " + sequenceName + " returned no value.");
        }
        return value;
    }
}
