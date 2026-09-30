/**
 * Workflow rules the UI must respect.
 *
 * These mirror the backend; they do not replace it. The server refuses an
 * invalid review with 409 regardless of what this file says, and the console
 * handles that. What this adds is not showing an operator a button that cannot
 * work — a disabled-by-reality action is a worse experience than an absent one.
 */

import type {
  ActorType,
  AuditEvent,
  InvestigationStatus,
  RootCauseClassification,
} from "../types";

/**
 * Only an investigation awaiting a decision can be reviewed.
 *
 * ESCALATED is deliberately excluded. The guardrail already routed those away
 * from recommendation review and toward a human investigating directly, which
 * is a different activity — the real INV-1003 ended ESCALATED and must not
 * offer an approve button.
 */
export function isReviewable(status: InvestigationStatus): boolean {
  return status === "AWAITING_REVIEW";
}

/** A run is in flight; the page should say so rather than look empty. */
export function isInProgress(status: InvestigationStatus): boolean {
  return status === "RUNNING";
}

/** Terminal states, where nothing further will happen on its own. */
export function isTerminal(status: InvestigationStatus): boolean {
  return status === "COMPLETED" || status === "ESCALATED" || status === "FAILED";
}

/** Classifications that are an admission rather than a conclusion. */
export function isNonConclusive(
  classification: RootCauseClassification,
): boolean {
  return classification === "UNKNOWN" || classification === "INSUFFICIENT_EVIDENCE";
}

export type Tone = "neutral" | "progress" | "attention" | "positive" | "warning" | "danger";

/**
 * Tone per status, for colour.
 *
 * ESCALATED is "warning", not "danger". Escalation is the system correctly
 * declining to present a weak explanation as a finding — a successful safety
 * outcome. Colouring it like a failure would teach operators to read the
 * guardrail working as the guardrail breaking.
 */
export function statusTone(status: InvestigationStatus): Tone {
  switch (status) {
    case "PENDING":
      return "neutral";
    case "RUNNING":
      return "progress";
    case "AWAITING_REVIEW":
      return "attention";
    case "COMPLETED":
      return "positive";
    case "ESCALATED":
      return "warning";
    case "FAILED":
      return "danger";
  }
}

export function actorTone(actor: ActorType): Tone {
  switch (actor) {
    case "SYSTEM":
      return "neutral";
    case "AI":
      return "progress";
    case "HUMAN":
      return "positive";
  }
}

/** Acronyms that must not be title-cased into "Ai" or "Fx". */
const ACRONYMS = new Set(["AI", "ID", "FX"]);

/** Human-readable label: "AWAITING_REVIEW" reads poorly in a UI. */
export function humanise(value: string): string {
  return value
    .split("_")
    .map((word) =>
      ACRONYMS.has(word) ? word : word.charAt(0) + word.slice(1).toLowerCase(),
    )
    .join(" ");
}

/** Short, plain-English description of each audit event. */
export function describeAuditEvent(event: AuditEvent): string {
  switch (event.event_type) {
    case "INVESTIGATION_CREATED":
      return "Investigation recorded from the reconciliation exception event.";
    case "INVESTIGATION_STARTED":
      return "Investigation claimed and evidence gathering began.";
    case "AI_RESULT_GENERATED":
      return "The model proposed an explanation, which passed evidence grounding.";
    case "INVESTIGATION_AWAITING_REVIEW":
      return "The deterministic guardrail routed this to a human reviewer.";
    case "INVESTIGATION_ESCALATED":
      return "The deterministic guardrail escalated this instead of routing it for approval.";
    case "INVESTIGATION_FAILED":
      return "The investigation ended without a usable result. Nothing was stored.";
    case "REVIEW_APPROVED":
      return "A human accepted the explanation.";
    case "REVIEW_REJECTED":
      return "A human did not accept the explanation.";
    // Operational, and deliberately worded so neither reads as a failure: the
    // investigation is back to PENDING in both cases.
    case "INVESTIGATION_RETRY_SCHEDULED":
      return "The model provider could not be reached; another attempt was scheduled.";
    case "INVESTIGATION_AUTO_RUN_PAUSED":
      return "Automatic investigation stopped without a conclusion; the exception is still queued.";
    default:
      return "";
  }
}

/**
 * The guardrail threshold that was actually applied, read from audit metadata.
 *
 * Read rather than assumed. The backend records `confidence_threshold` on the
 * routing event, so the console can show the real configured value instead of
 * hardcoding 0.85 — a magic number here would silently go stale the moment an
 * operator retuned the policy.
 *
 * Returns null when no routing event exists yet, and the UI says so.
 */
export function guardrailThresholdFrom(events: AuditEvent[]): string | null {
  for (const event of events) {
    if (
      event.event_type === "INVESTIGATION_ESCALATED" ||
      event.event_type === "INVESTIGATION_AWAITING_REVIEW"
    ) {
      const threshold = event.metadata?.["confidence_threshold"];
      if (typeof threshold === "string") return threshold;
      if (typeof threshold === "number") return String(threshold);
    }
  }
  return null;
}

/** The guardrail's own stated reason, as recorded at the time. */
export function guardrailReasonFrom(events: AuditEvent[]): string | null {
  for (const event of events) {
    if (
      event.event_type === "INVESTIGATION_ESCALATED" ||
      event.event_type === "INVESTIGATION_AWAITING_REVIEW"
    ) {
      const reason = event.metadata?.["reason"];
      if (typeof reason === "string") return reason;
    }
  }
  return null;
}

/**
 * Why a failed investigation failed, as the system recorded it at the time.
 *
 * Read from the audit trail rather than guessed at in the UI. The FAILED notice
 * previously asserted a cause — "the evidence did not support a conclusion, or a
 * cited reference could not be verified" — which is wrong for a malformed
 * structured result and wrong for a provider outage. INV-1004 was neither of the
 * two things the page claimed.
 *
 * Same shape as `guardrailReasonFrom`: the authoritative reason is already in
 * `INVESTIGATION_FAILED`'s metadata, so no new endpoint is needed.
 */
export function failureDetailFrom(events: AuditEvent[]): string | null {
  for (const event of events) {
    if (event.event_type !== "INVESTIGATION_FAILED") continue;
    const detail = event.metadata?.["detail"];
    if (typeof detail === "string" && detail.trim() !== "") return detail;
  }
  return null;
}

/**
 * Why automatic execution stopped without a conclusion, if it did.
 *
 * Derived from the audit trail, never guessed from how long an investigation
 * has sat PENDING — elapsed time says nothing about the cause, and a page that
 * inferred one would eventually say something false.
 *
 * Returns the reason code as recorded: `AI_BUDGET_EXHAUSTED` or
 * `PROVIDER_UNAVAILABLE`. Null means nothing paused, which is the normal case.
 */
export function autoRunPausedReasonFrom(events: AuditEvent[]): string | null {
  // Newest-relevant wins: an investigation can pause, be recovered, and pause
  // again, and the latest pause is the one that explains the current state.
  for (const event of events.slice().reverse()) {
    if (event.event_type !== "INVESTIGATION_AUTO_RUN_PAUSED") continue;
    const reason = event.metadata?.["reason"];
    if (typeof reason === "string" && reason.trim() !== "") return reason;
  }
  return null;
}

/** The failure's exception class, for readers who want the category. */
export function failureTypeFrom(events: AuditEvent[]): string | null {
  for (const event of events) {
    if (event.event_type !== "INVESTIGATION_FAILED") continue;
    const type = event.metadata?.["failure_type"];
    if (typeof type === "string" && type.trim() !== "") return type;
  }
  return null;
}

/**
 * True while the investigation may still change state on its own.
 *
 * PENDING counts: with automatic execution an investigation left PENDING is
 * waiting for a scheduled run or for the AI budget window to reset, so a client
 * watching it should keep watching.
 */
export function isSettled(status: InvestigationStatus): boolean {
  return isTerminal(status) || status === "AWAITING_REVIEW";
}

/** Counts per status, derived client-side from the list the backend returns. */
export function countByStatus(
  statuses: InvestigationStatus[],
): Record<InvestigationStatus, number> {
  const counts: Record<InvestigationStatus, number> = {
    PENDING: 0,
    RUNNING: 0,
    AWAITING_REVIEW: 0,
    COMPLETED: 0,
    ESCALATED: 0,
    FAILED: 0,
  };
  for (const status of statuses) counts[status] += 1;
  return counts;
}

/**
 * A human decision, reconstructed from the audit trail.
 *
 * The Investigation Service exposes no endpoint to fetch a review, so rather
 * than invent one the console reads what the audit trail already records: the
 * review events carry the decision, the review id and the resulting status, and
 * the actor fields carry who and when.
 *
 * This is the audit trail doing its job — the record of what happened is
 * sufficient to show what happened.
 */
export interface RecordedDecision {
  decision: "APPROVED" | "REJECTED" | "ESCALATED";
  reviewedBy: string | null;
  decidedAt: string;
  reviewId: string | null;
  resultingStatus: string | null;
}

export function recordedDecisionFrom(events: AuditEvent[]): RecordedDecision | null {
  for (const event of events) {
    if (event.actor_type !== "HUMAN") continue;

    const metadata = event.metadata ?? {};
    const raw = metadata["decision"];
    const decision =
      typeof raw === "string" && ["APPROVED", "REJECTED", "ESCALATED"].includes(raw)
        ? (raw as RecordedDecision["decision"])
        : event.event_type === "REVIEW_APPROVED"
          ? "APPROVED"
          : event.event_type === "REVIEW_REJECTED"
            ? "REJECTED"
            : null;
    if (decision === null) continue;

    return {
      decision,
      reviewedBy: event.actor_id,
      decidedAt: event.occurred_at,
      reviewId: typeof metadata["review_id"] === "string" ? metadata["review_id"] : null,
      resultingStatus:
        typeof metadata["resulting_status"] === "string" ? metadata["resulting_status"] : null,
    };
  }
  return null;
}
