package com.reconai.schema;

import com.reconai.support.PostgresIntegrationTest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThatCode;

/**
 * Verifies that the Flyway migrations produce the schema described in
 * docs/data-model.md, and that the invariants the schema is responsible for are
 * enforced by the database rather than by application convention alone.
 *
 * <p>These tests deliberately use raw SQL. They assert what the database guarantees
 * even if application code is wrong, bypassed or replaced.
 */
@Transactional
class SchemaMigrationIntegrationTest extends PostgresIntegrationTest {

    private static final OffsetDateTime NOW = OffsetDateTime.now(ZoneOffset.UTC);

    private final JdbcTemplate jdbc;

    @Autowired
    SchemaMigrationIntegrationTest(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Test
    void flywayAppliesAllThreeSchemaMigrationsSuccessfully() {
        List<String> applied = jdbc.queryForList(
                "SELECT version FROM flyway_schema_history WHERE success = true ORDER BY installed_rank",
                String.class);

        assertThat(applied).containsExactly("1", "2", "3");
    }

    @Test
    void migrationsCreateTheThreeFinancialCoreTables() {
        List<String> tables = jdbc.queryForList(
                "SELECT table_name FROM information_schema.tables "
                        + "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                        + "AND table_name <> 'flyway_schema_history' ORDER BY table_name",
                String.class);

        assertThat(tables).containsExactly("reconciliation_exceptions", "settlements", "transactions");
    }

    @Test
    void businessIdSequencesExistForEachEntity() {
        List<String> sequences = jdbc.queryForList(
                "SELECT sequence_name FROM information_schema.sequences "
                        + "WHERE sequence_schema = 'public' ORDER BY sequence_name",
                String.class);

        assertThat(sequences).containsExactly(
                "exception_business_id_seq",
                "settlement_business_id_seq",
                "transaction_business_id_seq");
    }

    @Test
    void allMonetaryColumnsUseNumeric19Scale4AndNeverFloatingPoint() {
        List<String> monetaryColumns = jdbc.queryForList(
                "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        + "WHERE table_schema = 'public' "
                        + "AND data_type IN ('double precision', 'real') ",
                String.class);

        assertThat(monetaryColumns)
                .as("no column may use a floating point type")
                .isEmpty();

        List<String> wrongPrecision = jdbc.queryForList(
                "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        + "WHERE table_schema = 'public' AND data_type = 'numeric' "
                        + "AND (numeric_precision <> 19 OR numeric_scale <> 4)",
                String.class);

        assertThat(wrongPrecision)
                .as("every monetary column must be NUMERIC(19,4)")
                .isEmpty();
    }

    // ---------------------------------------------------------------------
    // Architectural invariant: PROCESSOR_FEE is not a deterministic exception type
    // ---------------------------------------------------------------------

    @Test
    void processorFeeIsStructurallyRejectedAsAnExceptionType() {
        insertTransaction("TX-90001", "MERCHANT-104", "1247.50", "USD");
        insertSettlement("SET-90001", "TX-90001", "1217.50", "USD", "COMPLETED");

        assertThatThrownBy(() -> insertException(
                "EX-90001", "TX-90001", "SET-90001", "PROCESSOR_FEE",
                "1247.50", "1217.50", "30.00", "OPEN"))
                .as("PROCESSOR_FEE is a root-cause classification produced by "
                        + "investigation, never a deterministic discrepancy")
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void theFourDeterministicExceptionTypesAreAccepted() {
        insertTransaction("TX-90002", "MERCHANT-104", "100.00", "USD");
        insertSettlement("SET-90002", "TX-90002", "100.00", "USD", "COMPLETED");

        assertThatCode(() -> {
            insertException("EX-90002", "TX-90002", "SET-90002", "AMOUNT_MISMATCH",
                    "100.00", "90.00", "10.00", "OPEN");
            insertException("EX-90003", "TX-90002", null, "MISSING_SETTLEMENT",
                    "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, "OPEN");
            insertException("EX-90004", "TX-90002", null, "DUPLICATE_SETTLEMENT",
                    "1_COMPLETED_SETTLEMENT", "SET-90002,SET-90003", null, "OPEN");
            insertException("EX-90005", "TX-90002", "SET-90002", "CURRENCY_MISMATCH",
                    "USD", "EUR", null, "OPEN");
        }).doesNotThrowAnyException();
    }

    // ---------------------------------------------------------------------
    // Idempotency: the unresolved natural-key partial unique index
    // ---------------------------------------------------------------------

    @Test
    void duplicateUnresolvedExceptionWithSameNaturalKeyIsRejected() {
        insertTransaction("TX-90003", "MERCHANT-104", "1247.50", "USD");
        insertSettlement("SET-90004", "TX-90003", "1217.50", "USD", "COMPLETED");
        insertException("EX-90006", "TX-90003", "SET-90004", "AMOUNT_MISMATCH",
                "1247.50", "1217.50", "30.00", "OPEN");

        assertThatThrownBy(() -> insertException(
                "EX-90007", "TX-90003", "SET-90004", "AMOUNT_MISMATCH",
                "1247.50", "1217.50", "30.00", "OPEN"))
                .as("re-running reconciliation must not accumulate duplicate "
                        + "unresolved exceptions")
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void naturalKeyWithNullSettlementIdIsStillDeduplicated() {
        insertTransaction("TX-90004", "MERCHANT-104", "500.00", "USD");
        insertException("EX-90008", "TX-90004", null, "MISSING_SETTLEMENT",
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, "OPEN");

        assertThatThrownBy(() -> insertException(
                "EX-90009", "TX-90004", null, "MISSING_SETTLEMENT",
                "SETTLEMENT_PRESENT", "NO_SETTLEMENT", null, "OPEN"))
                .as("COALESCE in the index must make NULL settlement_id values "
                        + "compare equal")
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void resolvedExceptionDoesNotSuppressReDetectionOfTheSameDiscrepancy() {
        insertTransaction("TX-90005", "MERCHANT-104", "830.00", "USD");
        insertSettlement("SET-90005", "TX-90005", "800.00", "USD", "COMPLETED");
        insertException("EX-90010", "TX-90005", "SET-90005", "AMOUNT_MISMATCH",
                "830.00", "800.00", "30.00", "OPEN");

        jdbc.update("UPDATE reconciliation_exceptions SET status = 'RESOLVED' "
                + "WHERE exception_id = 'EX-90010'");

        assertThatCode(() -> insertException(
                "EX-90011", "TX-90005", "SET-90005", "AMOUNT_MISMATCH",
                "830.00", "800.00", "30.00", "OPEN"))
                .as("the partial index is scoped to unresolved exceptions, so a "
                        + "recurring discrepancy can be detected again")
                .doesNotThrowAnyException();
    }

    @Test
    void differentObservedValueProducesADistinctExceptionRatherThanACollision() {
        insertTransaction("TX-90006", "MERCHANT-104", "830.00", "USD");
        insertSettlement("SET-90006", "TX-90006", "800.00", "USD", "COMPLETED");
        insertException("EX-90012", "TX-90006", "SET-90006", "AMOUNT_MISMATCH",
                "830.00", "800.00", "30.00", "OPEN");

        assertThatCode(() -> insertException(
                "EX-90013", "TX-90006", "SET-90006", "AMOUNT_MISMATCH",
                "830.00", "795.00", "35.00", "OPEN"))
                .as("a genuinely different discrepancy is a different exception")
                .doesNotThrowAnyException();
    }

    // ---------------------------------------------------------------------
    // Cardinality and value constraints
    // ---------------------------------------------------------------------

    @Test
    void aTransactionMayHaveMultipleSettlementsSoDuplicatesAreDetectable() {
        insertTransaction("TX-90007", "MERCHANT-104", "850.00", "USD");

        assertThatCode(() -> {
            insertSettlement("SET-90007", "TX-90007", "850.00", "USD", "COMPLETED");
            insertSettlement("SET-90008", "TX-90007", "850.00", "USD", "COMPLETED");
        }).doesNotThrowAnyException();

        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM settlements WHERE transaction_id = 'TX-90007'",
                Integer.class);
        assertThat(count).isEqualTo(2);
    }

    @Test
    void settlementCannotReferenceANonexistentTransaction() {
        assertThatThrownBy(() -> insertSettlement(
                "SET-90009", "TX-DOES-NOT-EXIST", "100.00", "USD", "COMPLETED"))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void negativeTransactionAmountsAreRejected() {
        assertThatThrownBy(() -> insertTransaction(
                "TX-90008", "MERCHANT-104", "-1.00", "USD"))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void invalidTransactionStatusIsRejected() {
        assertThatThrownBy(() -> jdbc.update(
                "INSERT INTO transactions (id, transaction_id, merchant_id, amount, "
                        + "expected_settlement_amount, currency, transaction_type, status, "
                        + "transaction_timestamp, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), "TX-90009", "MERCHANT-104",
                new BigDecimal("100.00"), new BigDecimal("100.00"), "USD",
                "PURCHASE", "NOT_A_REAL_STATUS", NOW, NOW, NOW))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void currencyCodeMustBeExactlyThreeCharacters() {
        assertThatThrownBy(() -> insertTransaction(
                "TX-90010", "MERCHANT-104", "100.00", "US"))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void monetaryScaleDifferencesArePreservedAsEqualNumericValues() {
        insertTransaction("TX-90011", "MERCHANT-104", "100.00", "USD");
        insertSettlement("SET-90010", "TX-90011", "100.0000", "USD", "COMPLETED");

        Boolean equal = jdbc.queryForObject(
                "SELECT t.expected_settlement_amount = s.settled_amount "
                        + "FROM transactions t JOIN settlements s "
                        + "ON s.transaction_id = t.transaction_id "
                        + "WHERE t.transaction_id = 'TX-90011'",
                Boolean.class);

        assertThat(equal)
                .as("NUMERIC comparison ignores scale, matching BigDecimal.compareTo()")
                .isTrue();
    }

    // ---------------------------------------------------------------------
    // Fixtures
    // ---------------------------------------------------------------------

    private void insertTransaction(String transactionId, String merchantId,
                                   String expectedSettlementAmount, String currency) {
        jdbc.update(
                "INSERT INTO transactions (id, transaction_id, merchant_id, amount, "
                        + "expected_settlement_amount, currency, transaction_type, status, "
                        + "transaction_timestamp, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), transactionId, merchantId,
                new BigDecimal(expectedSettlementAmount),
                new BigDecimal(expectedSettlementAmount),
                currency, "PURCHASE", "POSTED", NOW, NOW, NOW);
    }

    private void insertSettlement(String settlementId, String transactionId,
                                  String settledAmount, String currency, String status) {
        jdbc.update(
                "INSERT INTO settlements (id, settlement_id, transaction_id, processor, "
                        + "settled_amount, currency, status, settlement_timestamp, created_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), settlementId, transactionId, "NORTHSTAR_PAYMENTS",
                new BigDecimal(settledAmount), currency, status, NOW, NOW);
    }

    private void insertException(String exceptionId, String transactionId, String settlementId,
                                 String exceptionType, String expectedValue, String observedValue,
                                 String differenceAmount, String status) {
        jdbc.update(
                "INSERT INTO reconciliation_exceptions (id, exception_id, transaction_id, "
                        + "settlement_id, exception_type, expected_value, observed_value, "
                        + "difference_amount, currency, status, detected_at, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                UUID.randomUUID(), exceptionId, transactionId, settlementId, exceptionType,
                expectedValue, observedValue,
                differenceAmount == null ? null : new BigDecimal(differenceAmount),
                "USD", status, NOW, NOW, NOW);
    }
}
