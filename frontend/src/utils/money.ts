/**
 * Formatting financial values without floating point.
 *
 * The backends send monetary amounts as decimal strings wherever it matters
 * ("2500.00"), and this module keeps them as strings the whole way to the
 * screen. `parseFloat("2500.10")` is not 2500.10, and a reconciliation console
 * whose displayed numbers disagree with the ledger by a cent is worse than one
 * that shows nothing.
 *
 * Where arithmetic is genuinely needed, it is done in integer minor units
 * (cents) via BigInt, never in `number`.
 */

/** Split a decimal string into sign, integer and fraction parts. */
function parseDecimal(
  value: string,
): { negative: boolean; whole: string; fraction: string } | null {
  const trimmed = value.trim();
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(trimmed);
  if (!match) return null;
  return {
    negative: match[1] === "-",
    whole: match[2],
    fraction: match[3] ?? "",
  };
}

/** Group an integer-part string with thousands separators. */
function groupThousands(whole: string): string {
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Render a decimal string as a fixed-precision amount.
 *
 * Truncation rather than rounding when the source has more precision than
 * requested: inventing a rounded figure in a reconciliation view would be
 * asserting a number the ledger does not contain. In practice the backend sends
 * exactly the precision it stores, so this path is defensive.
 */
export function formatDecimalString(value: string, precision = 2): string {
  const parsed = parseDecimal(value);
  if (parsed === null) return value;

  const fraction = parsed.fraction.padEnd(precision, "0").slice(0, precision);
  const grouped = groupThousands(parsed.whole);
  const sign = parsed.negative ? "-" : "";
  return precision > 0 ? `${sign}${grouped}.${fraction}` : `${sign}${grouped}`;
}

/**
 * Render an amount with its currency code.
 *
 * The code trails the number rather than using a locale currency symbol: an
 * operations console shows many currencies and "USD"/"EUR" is unambiguous where
 * "$" is not.
 */
export function formatMoney(
  value: string | number | null | undefined,
  currency?: string | null,
): string {
  if (value === null || value === undefined) return "—";
  const asString = typeof value === "number" ? numberToDecimalString(value) : value;
  const formatted = formatDecimalString(asString);
  return currency ? `${formatted} ${currency}` : formatted;
}

/**
 * Convert a JSON number to a decimal string.
 *
 * Needed only where a backend serialises an amount as a JSON number — Jackson
 * does this for `differenceAmount`. By the time the value reaches here it is
 * already a double, so this recovers the shortest exact representation rather
 * than pretending to restore precision that was lost upstream.
 */
export function numberToDecimalString(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return String(value);
}

/**
 * Difference between two decimal strings, computed in integer minor units.
 *
 * Used only as a cross-check and for display where the backend does not supply
 * a difference. Where it does, that value is preferred: the backend computed it
 * with BigDecimal against the authoritative records.
 */
export function subtractDecimalStrings(
  left: string,
  right: string,
): string | null {
  const a = parseDecimal(left);
  const b = parseDecimal(right);
  if (a === null || b === null) return null;

  const scale = Math.max(a.fraction.length, b.fraction.length);
  const toMinor = (parsed: { negative: boolean; whole: string; fraction: string }) => {
    const digits = `${parsed.whole}${parsed.fraction.padEnd(scale, "0")}`;
    const magnitude = BigInt(digits);
    return parsed.negative ? -magnitude : magnitude;
  };

  const difference = toMinor(a) - toMinor(b);
  const negative = difference < 0n;
  const absolute = (negative ? -difference : difference).toString().padStart(scale + 1, "0");
  const whole = absolute.slice(0, absolute.length - scale) || "0";
  const fraction = scale > 0 ? absolute.slice(absolute.length - scale) : "";

  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

/**
 * Render a confidence decimal string ("0.7000") as a percentage ("70%").
 *
 * Integer arithmetic on the digits, so 0.7000 shows as 70% rather than
 * 70.00000000000001%. Deliberately no decimal places: the underlying number is
 * a model self-report, and rendering it to two decimals would suggest a
 * precision it does not have.
 */
export function formatConfidence(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const parsed = parseDecimal(value);
  if (parsed === null) return value;

  const fraction = parsed.fraction.padEnd(4, "0").slice(0, 4);
  const basisPoints = BigInt(`${parsed.whole}${fraction}`);
  const percent = basisPoints / 100n;
  return `${parsed.negative ? "-" : ""}${percent}%`;
}

/** Confidence as a 0–100 number, for bar widths only — never for display. */
export function confidenceToPercent(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = parseDecimal(value);
  if (parsed === null) return 0;
  const fraction = parsed.fraction.padEnd(4, "0").slice(0, 4);
  return Number(BigInt(`${parsed.whole}${fraction}`) / 100n);
}
