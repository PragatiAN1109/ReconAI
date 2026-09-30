import type { DemoReconcileRequest } from "../types";

/**
 * Prefilled inputs for the reconciliation playground.
 *
 * These are not backend features. Each one is a JSON document that populates an
 * editable field, and the engine does not know which template a payload came
 * from — it applies the same rules to whatever arrives. Picking a scenario is a
 * shortcut past typing, nothing more.
 *
 * Four are offered because four are the deterministic exception types a single
 * transaction can produce, minus the duplicate case: DUPLICATE_SETTLEMENT needs
 * two settlements, which the payload shape allows and the engine still detects,
 * but which is a less obvious first thing to show someone.
 *
 * The amounts deliberately mirror the case in the project README — $2,500
 * expected, $2,450 settled, $50 short — so a reader who arrived from there sees
 * the numbers they were told about.
 */
export interface DemoScenario {
  id: string;
  label: string;
  /** What the deterministic engine should conclude. Shown as a hint, not sent. */
  expectation: string;
  payload: DemoReconcileRequest;
}

export const SCENARIOS: readonly DemoScenario[] = [
  {
    id: "matching",
    label: "Matching",
    expectation: "Reconciles cleanly — no exception, no investigation",
    payload: {
      transaction: {
        amount: "2500.00",
        expectedSettlementAmount: "2500.00",
        currency: "USD",
      },
      settlements: [{ settledAmount: "2500.00", currency: "USD" }],
    },
  },
  {
    id: "amount-mismatch",
    label: "Amount Mismatch",
    expectation: "AMOUNT_MISMATCH — $50.00 short",
    payload: {
      transaction: {
        amount: "2500.00",
        expectedSettlementAmount: "2500.00",
        currency: "USD",
      },
      settlements: [{ settledAmount: "2450.00", currency: "USD" }],
    },
  },
  {
    id: "currency-mismatch",
    label: "Currency Mismatch",
    expectation: "CURRENCY_MISMATCH — settled in the wrong currency",
    payload: {
      transaction: {
        amount: "2500.00",
        expectedSettlementAmount: "2500.00",
        currency: "USD",
      },
      settlements: [{ settledAmount: "2500.00", currency: "EUR" }],
    },
  },
  {
    id: "missing-settlement",
    label: "Missing Settlement",
    expectation: "MISSING_SETTLEMENT — nothing settled at all",
    payload: {
      transaction: {
        amount: "2500.00",
        expectedSettlementAmount: "2500.00",
        currency: "USD",
      },
      // Empty rather than omitted: the absence is the point of this scenario, and
      // showing the key makes that legible to someone reading the JSON.
      settlements: [],
    },
  },
] as const;

/** The template the editor opens with. */
export const DEFAULT_SCENARIO_ID = "amount-mismatch";

/** Pretty-printed JSON for the editor, at the indentation a human would type. */
export function scenarioJson(scenario: DemoScenario): string {
  return JSON.stringify(scenario.payload, null, 2);
}
