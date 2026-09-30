/** Calls against the Python Investigation Service. */

import type {
  AuditList,
  Investigation,
  InvestigationList,
  Recommendation,
  Review,
  RunResult,
} from "../types";
import { INVESTIGATION_BASE, get, getOptional, post } from "./client";

export function listInvestigations(): Promise<InvestigationList> {
  return get<InvestigationList>(`${INVESTIGATION_BASE}/investigations`);
}

export function getInvestigation(id: string): Promise<Investigation> {
  return get<Investigation>(`${INVESTIGATION_BASE}/investigations/${id}`);
}

/**
 * Null while an investigation is PENDING or RUNNING.
 *
 * The backend returns 404 in that case deliberately — there is genuinely no
 * conclusion yet, and an empty object would invite rendering one that nobody
 * reached.
 */
export function getRecommendation(id: string): Promise<Recommendation | null> {
  return getOptional<Recommendation>(
    `${INVESTIGATION_BASE}/investigations/${id}/recommendation`,
  );
}

export function getAuditTrail(id: string): Promise<AuditList> {
  return get<AuditList>(`${INVESTIGATION_BASE}/investigations/${id}/audit`);
}

/**
 * The investigation recorded for an exception, or null if there is not one yet.
 *
 * An exception reaches this service over Kafka, so between the Financial Core
 * detecting a discrepancy and the investigation existing there is a real window.
 * Null means "not yet", which is a normal stage rather than an error — the
 * backend returns an empty list for exactly that reason.
 *
 * Filtered server-side rather than by fetching every investigation and
 * searching: the collection grows with every discrepancy ever detected, and a
 * caller polling it would get slower the longer the demo has been live.
 */
export async function findInvestigationByExceptionId(
  exceptionId: string,
): Promise<Investigation | null> {
  const response = await get<InvestigationList>(
    `${INVESTIGATION_BASE}/investigations?exception_id=${encodeURIComponent(exceptionId)}`,
  );
  return response.items[0] ?? null;
}

/**
 * Run the AI investigation for one PENDING investigation.
 *
 * Deliberately a separate, explicit action. Detecting a discrepancy is
 * deterministic and free; investigating one calls a language model and costs
 * money, so nothing triggers it implicitly — not reconciliation, not Kafka
 * consumption, not opening this page.
 *
 * The server refuses a second run with 409 and throttles the endpoint with 429,
 * both of which the caller is expected to surface rather than retry.
 */
export function runInvestigation(id: string): Promise<RunResult> {
  return post<RunResult>(`${INVESTIGATION_BASE}/investigations/${id}/run`);
}

export type ReviewAction = "approve" | "reject" | "escalate";

/**
 * Record a human decision.
 *
 * This writes only to the Investigation Service's own tables. It does not
 * resolve the reconciliation exception, alter a settlement, or move money —
 * no code path from this console can.
 */
export function submitReview(
  id: string,
  action: ReviewAction,
  reviewedBy: string,
  comment?: string,
): Promise<Review> {
  return post<Review>(`${INVESTIGATION_BASE}/investigations/${id}/${action}`, {
    reviewed_by: reviewedBy,
    comment: comment?.trim() ? comment.trim() : null,
  });
}
