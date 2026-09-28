/** Calls against the Python Investigation Service. */

import type {
  AuditList,
  Investigation,
  InvestigationList,
  Recommendation,
  Review,
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
