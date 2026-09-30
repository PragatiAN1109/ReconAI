import { describe, expect, it } from "vitest";

import type { AuditEvent, InvestigationStatus } from "../types";
import {
  autoRunPausedReasonFrom,
  countByStatus,
  describeAuditEvent,
  failureDetailFrom,
  failureTypeFrom,
  guardrailReasonFrom,
  guardrailThresholdFrom,
  humanise,
  isNonConclusive,
  isReviewable,
  isSettled,
  recordedDecisionFrom,
  statusTone,
} from "./workflow";

function event(
  type: AuditEvent["event_type"],
  actor: AuditEvent["actor_type"],
  metadata: Record<string, unknown> | null = null,
  actorId: string | null = null,
): AuditEvent {
  return {
    event_id: `AUD-${type}`,
    investigation_id: "INV-1003",
    event_type: type,
    actor_type: actor,
    actor_id: actorId,
    metadata,
    occurred_at: "2026-09-27T16:44:35Z",
  };
}

/**
 * Review eligibility is the safety-critical rule in this UI: offering approval
 * on a case the guardrail escalated would present the guardrail's correct
 * decision as something a click can undo.
 */
describe("isReviewable", () => {
  it("allows review only while awaiting a decision", () => {
    expect(isReviewable("AWAITING_REVIEW")).toBe(true);
  });

  it("refuses every other state", () => {
    const others: InvestigationStatus[] = [
      "PENDING",
      "RUNNING",
      "COMPLETED",
      "ESCALATED",
      "FAILED",
    ];
    for (const status of others) {
      expect(isReviewable(status)).toBe(false);
    }
  });

  it("refuses an escalated investigation, as the real INV-1003 is", () => {
    // The guardrail routed it away from recommendation review; there is
    // nothing to approve.
    expect(isReviewable("ESCALATED")).toBe(false);
  });
});

describe("statusTone", () => {
  it("does not colour escalation as a failure", () => {
    // Escalation is the guardrail working. Only FAILED is a fault.
    expect(statusTone("ESCALATED")).toBe("warning");
    expect(statusTone("FAILED")).toBe("danger");
    expect(statusTone("COMPLETED")).toBe("positive");
    expect(statusTone("AWAITING_REVIEW")).toBe("attention");
  });
});

describe("isNonConclusive", () => {
  it("treats admissions as non-conclusive", () => {
    expect(isNonConclusive("UNKNOWN")).toBe(true);
    expect(isNonConclusive("INSUFFICIENT_EVIDENCE")).toBe(true);
    expect(isNonConclusive("PROCESSOR_FEE")).toBe(false);
  });
});

describe("humanise", () => {
  it("makes enum values readable", () => {
    expect(humanise("AWAITING_REVIEW")).toBe("Awaiting Review");
    expect(humanise("PROCESSOR_FEE")).toBe("Processor Fee");
    expect(humanise("AMOUNT_MISMATCH")).toBe("Amount Mismatch");
  });
});

/**
 * The threshold is read from audit metadata rather than hardcoded, so the UI
 * shows the value actually applied even if an operator retunes the policy.
 */
describe("guardrailThresholdFrom", () => {
  it("reads the threshold that was really applied", () => {
    const events = [
      event("INVESTIGATION_STARTED", "SYSTEM"),
      event("AI_RESULT_GENERATED", "AI", { confidence: "0.7000" }),
      event("INVESTIGATION_ESCALATED", "SYSTEM", { confidence_threshold: "0.85" }),
    ];
    expect(guardrailThresholdFrom(events)).toBe("0.85");
  });

  it("reads it from the awaiting-review event too", () => {
    const events = [event("INVESTIGATION_AWAITING_REVIEW", "SYSTEM", { confidence_threshold: "0.90" })];
    expect(guardrailThresholdFrom(events)).toBe("0.90");
  });

  it("returns null rather than inventing a default", () => {
    expect(guardrailThresholdFrom([event("INVESTIGATION_STARTED", "SYSTEM")])).toBeNull();
  });
});

describe("guardrailReasonFrom", () => {
  it("surfaces the reason recorded at the time", () => {
    const events = [
      event("INVESTIGATION_ESCALATED", "SYSTEM", {
        reason: "Reported confidence 0.7000 is below the review threshold 0.85.",
      }),
    ];
    expect(guardrailReasonFrom(events)).toContain("below the review threshold");
  });
});

/**
 * There is no endpoint to fetch a review, so the decision is reconstructed from
 * the audit trail rather than by inventing a backend route.
 */
describe("recordedDecisionFrom", () => {
  it("finds an approval recorded by a human", () => {
    const events = [
      event("INVESTIGATION_STARTED", "SYSTEM"),
      event(
        "REVIEW_APPROVED",
        "HUMAN",
        { decision: "APPROVED", review_id: "REV-7001", resulting_status: "COMPLETED" },
        "ops.analyst",
      ),
    ];

    const decision = recordedDecisionFrom(events);
    expect(decision).not.toBeNull();
    expect(decision!.decision).toBe("APPROVED");
    expect(decision!.reviewedBy).toBe("ops.analyst");
    expect(decision!.reviewId).toBe("REV-7001");
  });

  it("finds a rejection", () => {
    const events = [event("REVIEW_REJECTED", "HUMAN", { decision: "REJECTED" }, "ops.analyst")];
    expect(recordedDecisionFrom(events)!.decision).toBe("REJECTED");
  });

  it("ignores system and AI events", () => {
    const events = [
      event("INVESTIGATION_STARTED", "SYSTEM"),
      event("AI_RESULT_GENERATED", "AI", { classification: "PROCESSOR_FEE" }),
      event("INVESTIGATION_ESCALATED", "SYSTEM", { reason: "below threshold" }),
    ];
    // A guardrail escalation is not a human decision and must not be shown as one.
    expect(recordedDecisionFrom(events)).toBeNull();
  });
});

describe("countByStatus", () => {
  it("derives queue counts client-side", () => {
    const counts = countByStatus(["PENDING", "PENDING", "ESCALATED", "COMPLETED"]);
    expect(counts.PENDING).toBe(2);
    expect(counts.ESCALATED).toBe(1);
    expect(counts.COMPLETED).toBe(1);
    expect(counts.RUNNING).toBe(0);
  });

  it("returns every status so cards never show undefined", () => {
    const counts = countByStatus([]);
    expect(Object.values(counts).every((value) => value === 0)).toBe(true);
    expect(Object.keys(counts)).toHaveLength(6);
  });
});

describe("humanise acronyms", () => {
  it("does not title-case AI into 'Ai'", () => {
    expect(humanise("AI_RESULT_GENERATED")).toBe("AI Result Generated");
  });

  it("still title-cases ordinary words", () => {
    expect(humanise("INVESTIGATION_ESCALATED")).toBe("Investigation Escalated");
  });
});

describe("failureDetailFrom", () => {
  /**
   * The FAILED notice used to assert a cause it could not know — "the evidence
   * did not support a conclusion, or a cited reference could not be verified" —
   * which was wrong for INV-1004, a malformed structured result. The real
   * reason is already in the audit trail.
   */
  function failure(metadata: Record<string, unknown> | null): AuditEvent {
    return {
      event_id: "AUD-1",
      investigation_id: "INV-1004",
      event_type: "INVESTIGATION_FAILED",
      actor_type: "SYSTEM",
      actor_id: null,
      metadata,
      occurred_at: "2026-09-30T10:00:00Z",
    };
  }

  it("returns the recorded detail", () => {
    expect(
      failureDetailFrom([failure({ detail: "returned a malformed result" })]),
    ).toBe("returned a malformed result");
  });

  it("returns the failure type separately, for the category", () => {
    expect(
      failureTypeFrom([failure({ failure_type: "UngroundedResultError" })]),
    ).toBe("UngroundedResultError");
  });

  it("returns null when no failure was recorded, so the UI can fall back", () => {
    expect(failureDetailFrom([])).toBeNull();
  });

  it("returns null rather than an empty string", () => {
    // An empty detail must not render as a blank sentence.
    expect(failureDetailFrom([failure({ detail: "   " })])).toBeNull();
  });

  it("ignores metadata that is absent or the wrong shape", () => {
    expect(failureDetailFrom([failure(null)])).toBeNull();
    expect(failureDetailFrom([failure({ detail: 42 })])).toBeNull();
  });

  it("ignores events that are not failures", () => {
    const escalated: AuditEvent = {
      ...failure({ detail: "not a failure" }),
      event_type: "INVESTIGATION_ESCALATED",
    };
    expect(failureDetailFrom([escalated])).toBeNull();
  });
});

describe("isSettled", () => {
  /**
   * With automatic execution, PENDING is no longer a resting place: something
   * will move it, so a client watching an investigation must keep watching.
   */
  it("treats PENDING and RUNNING as still moving", () => {
    expect(isSettled("PENDING")).toBe(false);
    expect(isSettled("RUNNING")).toBe(false);
  });

  it("treats a routed or decided investigation as settled", () => {
    expect(isSettled("AWAITING_REVIEW")).toBe(true);
    expect(isSettled("ESCALATED")).toBe(true);
    expect(isSettled("COMPLETED")).toBe(true);
    expect(isSettled("FAILED")).toBe(true);
  });

  it("covers every status, so a new one cannot be silently unhandled", () => {
    const statuses: InvestigationStatus[] = [
      "PENDING",
      "RUNNING",
      "AWAITING_REVIEW",
      "COMPLETED",
      "FAILED",
      "ESCALATED",
    ];
    expect(statuses.filter((status) => !isSettled(status))).toEqual(["PENDING", "RUNNING"]);
  });
});

describe("autoRunPausedReasonFrom", () => {
  /**
   * The PENDING copy must be derived from what was recorded, never inferred
   * from how long an investigation has sat there — elapsed time says nothing
   * about the cause.
   */
  function paused(metadata: Record<string, unknown> | null): AuditEvent {
    return {
      event_id: "AUD-2",
      investigation_id: "INV-2001",
      event_type: "INVESTIGATION_AUTO_RUN_PAUSED",
      actor_type: "SYSTEM",
      actor_id: null,
      metadata,
      occurred_at: "2026-09-30T10:00:00Z",
    };
  }

  it("returns the recorded budget reason", () => {
    expect(autoRunPausedReasonFrom([paused({ reason: "AI_BUDGET_EXHAUSTED" })])).toBe(
      "AI_BUDGET_EXHAUSTED",
    );
  });

  it("returns the recorded provider reason", () => {
    expect(autoRunPausedReasonFrom([paused({ reason: "PROVIDER_UNAVAILABLE" })])).toBe(
      "PROVIDER_UNAVAILABLE",
    );
  });

  it("returns null when nothing paused, which is the normal case", () => {
    expect(autoRunPausedReasonFrom([])).toBeNull();
  });

  it("ignores a scheduled retry, which is not a pause", () => {
    const retry: AuditEvent = {
      ...paused({ attempt: 1 }),
      event_type: "INVESTIGATION_RETRY_SCHEDULED",
    };
    expect(autoRunPausedReasonFrom([retry])).toBeNull();
  });

  it("prefers the most recent pause when an investigation paused twice", () => {
    // Recovered by an operator, then paused again: the latest explains now.
    expect(
      autoRunPausedReasonFrom([
        paused({ reason: "PROVIDER_UNAVAILABLE" }),
        paused({ reason: "AI_BUDGET_EXHAUSTED" }),
      ]),
    ).toBe("AI_BUDGET_EXHAUSTED");
  });

  it("ignores metadata that is absent or the wrong shape", () => {
    expect(autoRunPausedReasonFrom([paused(null)])).toBeNull();
    expect(autoRunPausedReasonFrom([paused({ reason: 7 })])).toBeNull();
    expect(autoRunPausedReasonFrom([paused({ reason: "  " })])).toBeNull();
  });
});

describe("describeAuditEvent for operational states", () => {
  function event(type: AuditEvent["event_type"]): AuditEvent {
    return {
      event_id: "AUD-3",
      investigation_id: "INV-2001",
      event_type: type,
      actor_type: "SYSTEM",
      actor_id: null,
      metadata: null,
      occurred_at: "2026-09-30T10:00:00Z",
    };
  }

  it("describes a scheduled retry without calling it a failure", () => {
    const text = describeAuditEvent(event("INVESTIGATION_RETRY_SCHEDULED"));
    expect(text).toMatch(/another attempt was scheduled/i);
    expect(text).not.toMatch(/failed/i);
  });

  it("describes a pause as still queued rather than as a failure", () => {
    const text = describeAuditEvent(event("INVESTIGATION_AUTO_RUN_PAUSED"));
    expect(text).toMatch(/still queued/i);
    expect(text).not.toMatch(/failed/i);
  });
});
