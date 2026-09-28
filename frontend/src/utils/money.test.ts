import { describe, expect, it } from "vitest";

import {
  confidenceToPercent,
  formatConfidence,
  formatDecimalString,
  formatMoney,
  subtractDecimalStrings,
} from "./money";

/**
 * Money formatting is the one place a UI bug becomes a financial one. A console
 * that displays an amount differing from the ledger by a cent is worse than one
 * that displays nothing, so these tests target the cases where naive
 * `parseFloat` arithmetic silently goes wrong.
 */

describe("formatDecimalString", () => {
  it("preserves decimal strings exactly", () => {
    expect(formatDecimalString("2500.00")).toBe("2,500.00");
    expect(formatDecimalString("2450.00")).toBe("2,450.00");
    expect(formatDecimalString("0.01")).toBe("0.01");
  });

  it("does not lose precision that float parsing would", () => {
    // parseFloat("2500.10") * 100 is 250009.99999999997.
    expect(formatDecimalString("2500.10")).toBe("2,500.10");
    expect(formatDecimalString("0.30")).toBe("0.30");
    expect(formatDecimalString("1234567.89")).toBe("1,234,567.89");
  });

  it("pads and groups", () => {
    expect(formatDecimalString("5")).toBe("5.00");
    expect(formatDecimalString("1000000")).toBe("1,000,000.00");
  });

  it("keeps the sign", () => {
    expect(formatDecimalString("-50.00")).toBe("-50.00");
  });

  it("returns unparseable input untouched rather than inventing a number", () => {
    expect(formatDecimalString("not-a-number")).toBe("not-a-number");
  });
});

describe("formatMoney", () => {
  it("appends the currency code", () => {
    expect(formatMoney("2500.00", "USD")).toBe("2,500.00 USD");
    expect(formatMoney("920.00", "EUR")).toBe("920.00 EUR");
  });

  it("renders a missing amount as a dash, never as zero", () => {
    // Showing 0.00 for an unknown amount would assert a fact we do not have.
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
  });

  it("accepts a JSON number, as Jackson sends differenceAmount", () => {
    expect(formatMoney(50, "USD")).toBe("50.00 USD");
    expect(formatMoney(50.0, "USD")).toBe("50.00 USD");
  });
});

describe("subtractDecimalStrings", () => {
  it("computes the real vertical-test difference", () => {
    expect(subtractDecimalStrings("2500.00", "2450.00")).toBe("50.00");
  });

  it("is exact where floating point is not", () => {
    // 0.3 - 0.1 === 0.19999999999999998 in IEEE-754 doubles.
    expect(subtractDecimalStrings("0.30", "0.10")).toBe("0.20");
    expect(subtractDecimalStrings("2500.10", "2450.05")).toBe("50.05");
  });

  it("handles differing scales", () => {
    expect(subtractDecimalStrings("100", "99.97")).toBe("0.03");
  });

  it("produces a negative difference when settled exceeds expected", () => {
    expect(subtractDecimalStrings("1000.00", "1025.00")).toBe("-25.00");
  });

  it("returns null rather than guessing on bad input", () => {
    expect(subtractDecimalStrings("abc", "1.00")).toBeNull();
  });
});

describe("formatConfidence", () => {
  it("renders the real INV-1003 confidence", () => {
    expect(formatConfidence("0.7000")).toBe("70%");
  });

  it("renders without floating point artefacts", () => {
    // (0.7 * 100) is 70.00000000000001 as a double.
    expect(formatConfidence("0.8500")).toBe("85%");
    expect(formatConfidence("0.8600")).toBe("86%");
    expect(formatConfidence("1.0000")).toBe("100%");
    expect(formatConfidence("0.0000")).toBe("0%");
  });

  it("shows a dash when absent", () => {
    expect(formatConfidence(null)).toBe("—");
  });
});

describe("confidenceToPercent", () => {
  it("converts for bar widths only", () => {
    expect(confidenceToPercent("0.7000")).toBe(70);
    expect(confidenceToPercent("0.8500")).toBe(85);
    expect(confidenceToPercent(null)).toBe(0);
  });
});
