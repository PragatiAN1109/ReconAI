package com.reconai.common.money;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * Helpers for the two scales monetary values move between.
 *
 * <p>Money is stored as {@code NUMERIC(19,4)} and handled as {@link BigDecimal}.
 * Floating point types are never used for financial values, and equality is always
 * decided with {@link BigDecimal#compareTo} so that {@code 100.00} and {@code 100.0000}
 * are the same amount.
 */
public final class Money {

    /** Scale of every monetary column in the schema. */
    public static final int STORAGE_SCALE = 4;

    /** Minimum scale used when serialising, so amounts read as 1247.50 rather than 1247.5. */
    private static final int MINIMUM_RESPONSE_SCALE = 2;

    private Money() {
    }

    /**
     * Normalises a value to the scale of the database column.
     *
     * <p>Applied before persisting so that the object returned by a write and the object
     * later read back are identical. Uses {@link RoundingMode#UNNECESSARY}: input with
     * more than four decimal places is rejected by validation at the API boundary, so
     * reaching this method with such a value is a defect and should fail loudly rather
     * than silently round someone's money.
     */
    public static BigDecimal toStorageScale(BigDecimal value) {
        return value == null ? null : value.setScale(STORAGE_SCALE, RoundingMode.UNNECESSARY);
    }

    /**
     * Formats a value for a JSON response.
     *
     * <p>Trailing zeros beyond two decimal places are dropped, so a stored
     * {@code 1247.5000} is serialised as {@code 1247.50} and a stored {@code 1217.5678}
     * keeps all four places. This matches the examples throughout
     * docs/api-contract.md without discarding precision.
     */
    public static BigDecimal forResponse(BigDecimal value) {
        if (value == null) {
            return null;
        }
        BigDecimal stripped = value.stripTrailingZeros();
        return stripped.scale() < MINIMUM_RESPONSE_SCALE
                ? stripped.setScale(MINIMUM_RESPONSE_SCALE, RoundingMode.UNNECESSARY)
                : stripped;
    }

    /** Returns true when both values represent the same amount, ignoring scale. */
    public static boolean isEqual(BigDecimal left, BigDecimal right) {
        if (left == null || right == null) {
            return left == right;
        }
        return left.compareTo(right) == 0;
    }
}
