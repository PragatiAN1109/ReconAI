/** The reconciliation playground's one call against the Financial Core. */

import type { DemoReconcileRequest, DemoReconcileResponse } from "../types";
import { CORE_BASE, post } from "./client";

/**
 * Create a synthetic transaction and its settlements, then reconcile them.
 *
 * The only write this console performs against the Financial Core. It creates
 * records and reports a deterministic verdict; it cannot amend or remove
 * anything, and it never reaches the Investigation Service. Whether the
 * resulting exception is investigated is a separate, human-initiated step.
 */
export function runDemoReconciliation(
  request: DemoReconcileRequest,
): Promise<DemoReconcileResponse> {
  return post<DemoReconcileResponse>(`${CORE_BASE}/demo/reconcile`, request);
}
