package com.reconai.common.money;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Unit tests for the monetary scale rules shared by every financial value. */
class MoneyTest {

    @Test
    void storageScaleAlwaysMatchesTheNumericColumnDefinition() {
        assertThat(Money.toStorageScale(new BigDecimal("1247.5"))).isEqualTo(new BigDecimal("1247.5000"));
        assertThat(Money.toStorageScale(new BigDecimal("100"))).isEqualTo(new BigDecimal("100.0000"));
        assertThat(Money.toStorageScale(new BigDecimal("0.0001"))).isEqualTo(new BigDecimal("0.0001"));
    }

    @Test
    void storageScaleRefusesToSilentlyRoundAwayPrecision() {
        assertThatThrownBy(() -> Money.toStorageScale(new BigDecimal("1247.56789")))
                .as("losing a fraction of a cent must be a loud failure, never silent")
                .isInstanceOf(ArithmeticException.class);
    }

    @Test
    void responseScaleShowsAtLeastTwoDecimalPlaces() {
        assertThat(Money.forResponse(new BigDecimal("1247.5000"))).hasToString("1247.50");
        assertThat(Money.forResponse(new BigDecimal("100.0000"))).hasToString("100.00");
        assertThat(Money.forResponse(new BigDecimal("30"))).hasToString("30.00");
    }

    @Test
    void responseScaleKeepsPrecisionBeyondTwoDecimalPlaces() {
        assertThat(Money.forResponse(new BigDecimal("1217.5678"))).hasToString("1217.5678");
        assertThat(Money.forResponse(new BigDecimal("0.0001"))).hasToString("0.0001");
    }

    @Test
    void equalityIgnoresScaleDifferences() {
        assertThat(Money.isEqual(new BigDecimal("100.00"), new BigDecimal("100.0000"))).isTrue();
        assertThat(Money.isEqual(new BigDecimal("1247.50"), new BigDecimal("1217.50"))).isFalse();
    }

    @Test
    void nullValuesArePropagatedRatherThanCoercedToZero() {
        assertThat(Money.toStorageScale(null)).isNull();
        assertThat(Money.forResponse(null)).isNull();
    }
}
