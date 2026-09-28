import { useState } from "react";

import type { Investigation } from "../types";
import type { ReviewAction } from "../api/investigations";
import { submitReview } from "../api/investigations";
import { ApiError } from "../api/client";
import type { RecordedDecision } from "../utils/workflow";
import { isReviewable } from "../utils/workflow";
import { formatTimestamp } from "../utils/datetime";
import { Badge } from "./Badge";
import { Notice, describeError } from "./StateViews";

/**
 * Human review — where authority lives.
 *
 * Three things this deliberately does not do:
 *
 * 1. **No optimistic state.** Nothing changes on screen until the backend has
 *    accepted the decision and the parent has re-fetched. A console that shows
 *    "Approved" before the server agrees is lying to an auditor.
 * 2. **No action for an unreviewable state.** The backend refuses with 409
 *    regardless, but offering a button that cannot work is its own defect.
 *    ESCALATED investigations get an explanation instead.
 * 3. **No claim that this moves money.** Approval records a judgement about an
 *    explanation. It resolves no exception and alters no ledger.
 */

const ACTION_COPY: Record<
  ReviewAction,
  { label: string; title: string; body: string; button: string; danger?: boolean }
> = {
  approve: {
    label: "Approve",
    title: "Approve this recommendation?",
    body:
      "This records that you accept the AI's explanation and completes the " +
      "investigation. It does not resolve the reconciliation exception, alter " +
      "any settlement, or move money.",
    button: "Record approval",
  },
  reject: {
    label: "Reject",
    title: "Reject this recommendation?",
    body:
      "This records that you do not accept the explanation. The investigation " +
      "is escalated rather than resolved — the underlying discrepancy still " +
      "exists and still needs a human.",
    button: "Record rejection",
    danger: true,
  },
  escalate: {
    label: "Escalate",
    title: "Escalate this investigation?",
    body:
      "This records that you are passing the case on rather than deciding it. " +
      "Distinct from rejection: the explanation is not judged wrong, only above " +
      "this reviewer's authority.",
    button: "Record escalation",
  },
};

interface Props {
  investigation: Investigation;
  /** Reconstructed from the audit trail; there is no endpoint to fetch it. */
  recordedDecision: RecordedDecision | null;
  onReviewed: () => void | Promise<void>;
}

export function ReviewPanel({ investigation, recordedDecision, onReviewed }: Props) {
  const [pending, setPending] = useState<ReviewAction | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const reviewable = isReviewable(investigation.status);

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      await submitReview(investigation.investigation_id, pending, reviewer.trim(), comment);
      setPending(null);
      setComment("");
      // The parent re-fetches. Nothing is assumed about the resulting state.
      await onReviewed();
    } catch (caught) {
      setError(caught);
      setPending(null);
      if (caught instanceof ApiError && caught.isConflict) {
        // Someone else decided first, or the state moved. Re-read the truth.
        await onReviewed();
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Human review</h2>
        <span className="origin-tag human">Human authority</span>
        <span className="card-note">Approval records a judgement, not a ledger change</span>
      </div>
      <div className="card-body">
        {error !== null && <ReviewError error={error} />}

        {recordedDecision ? (
          <DecisionRecord decision={recordedDecision} />
        ) : reviewable ? (
          <ReviewForm
            reviewer={reviewer}
            comment={comment}
            busy={busy}
            onReviewer={setReviewer}
            onComment={setComment}
            onAction={setPending}
          />
        ) : (
          <NotReviewable status={investigation.status} />
        )}
      </div>

      {pending && (
        <ConfirmDialog
          action={pending}
          reviewer={reviewer}
          busy={busy}
          onCancel={() => setPending(null)}
          onConfirm={confirm}
        />
      )}
    </section>
  );
}

function ReviewError({ error }: { error: unknown }) {
  const { title, detail } = describeError(error);
  const conflict = error instanceof ApiError && error.isConflict;
  return (
    <Notice tone={conflict ? "warning" : "error"} title={conflict ? "No longer reviewable" : title}>
      {conflict
        ? `${detail} The current state has been reloaded.`
        : detail}
    </Notice>
  );
}

function DecisionRecord({ decision }: { decision: RecordedDecision }) {
  const tone =
    decision.decision === "APPROVED"
      ? "positive"
      : decision.decision === "REJECTED"
        ? "danger"
        : "warning";
  return (
    <div className="stack">
      <div className="detail-grid">
        <Field label="Decision">
          <Badge tone={tone} label={decision.decision} />
        </Field>
        <Field label="Reviewed by">
          <span className="mono">{decision.reviewedBy ?? "—"}</span>
        </Field>
        <Field label="Decided at">{formatTimestamp(decision.decidedAt)}</Field>
        {decision.reviewId && (
          <Field label="Review ID">
            <span className="mono">{decision.reviewId}</span>
          </Field>
        )}
      </div>
      <p className="caveat">
        Reviewer identity is caller-supplied and unverified: this console has no
        authentication. A decision is recorded once and is not replaced.
      </p>
    </div>
  );
}

function NotReviewable({ status }: { status: Investigation["status"] }) {
  const explanation: Record<string, string> = {
    PENDING: "This investigation has not been run yet, so there is no conclusion to review.",
    RUNNING: "This investigation is in progress. A decision becomes possible once it concludes.",
    ESCALATED:
      "The deterministic guardrail escalated this investigation rather than routing it for " +
      "approval, so there is no recommendation to approve. It needs a human to investigate " +
      "directly — a different activity from accepting a proposed explanation.",
    COMPLETED: "A human decision has already been recorded for this investigation.",
    FAILED:
      "This investigation ended without a usable result, and nothing was stored. There is " +
      "nothing to review.",
  };

  return (
    <div>
      <p className="section-note" style={{ marginBottom: 0 }}>
        {explanation[status] ?? "This investigation is not in a reviewable state."}
      </p>
    </div>
  );
}

function ReviewForm({
  reviewer,
  comment,
  busy,
  onReviewer,
  onComment,
  onAction,
}: {
  reviewer: string;
  comment: string;
  busy: boolean;
  onReviewer: (value: string) => void;
  onComment: (value: string) => void;
  onAction: (action: ReviewAction) => void;
}) {
  const named = reviewer.trim().length > 0;

  return (
    <div>
      <div className="field">
        <label htmlFor="reviewer">Reviewer</label>
        <input
          id="reviewer"
          value={reviewer}
          onChange={(event) => onReviewer(event.target.value)}
          placeholder="ops.analyst"
          autoComplete="off"
        />
        <div className="field-hint">
          Demo attribution only. This console has no authentication, and the backend
          records whatever is entered here without verifying it.
        </div>
      </div>

      <div className="field">
        <label htmlFor="comment">Comment (optional)</label>
        <textarea
          id="comment"
          rows={3}
          value={comment}
          onChange={(event) => onComment(event.target.value)}
          placeholder="Why this decision was made."
        />
      </div>

      <div className="review-actions">
        <button
          className="btn primary"
          disabled={!named || busy}
          onClick={() => onAction("approve")}
        >
          Approve
        </button>
        <button className="btn danger" disabled={!named || busy} onClick={() => onAction("reject")}>
          Reject
        </button>
        <button className="btn" disabled={!named || busy} onClick={() => onAction("escalate")}>
          Escalate
        </button>
      </div>
      {!named && <div className="field-hint">Enter a reviewer name to record a decision.</div>}
    </div>
  );
}

function ConfirmDialog({
  action,
  reviewer,
  busy,
  onCancel,
  onConfirm,
}: {
  action: ReviewAction;
  reviewer: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const copy = ACTION_COPY[action];
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-head">
          <h2 className="dialog-title" id="confirm-title">
            {copy.title}
          </h2>
        </div>
        <div className="dialog-body">
          <p style={{ marginTop: 0 }}>{copy.body}</p>
          <p className="caveat">
            Recorded as <strong>{reviewer.trim()}</strong>. This identity is not
            authenticated and cannot be changed after the decision is stored.
          </p>
        </div>
        <div className="dialog-foot">
          <button className="btn ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className={`btn ${copy.danger ? "danger" : "primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "Recording…" : copy.button}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="detail-item">
      <div className="detail-key">{label}</div>
      <div className="detail-value">{children}</div>
    </div>
  );
}
