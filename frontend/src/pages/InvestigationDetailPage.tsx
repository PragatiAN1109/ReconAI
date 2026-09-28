import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type {
  AuditEvent,
  Investigation,
  ReconciliationException,
  Recommendation,
  Settlement,
  Transaction,
} from "../types";
import {
  getAuditTrail,
  getInvestigation,
  getRecommendation,
} from "../api/investigations";
import { getException, getSettlements, getTransaction } from "../api/financialCore";
import { ApiError } from "../api/client";
import {
  confidenceToPercent,
  formatConfidence,
  formatMoney,
  numberToDecimalString,
  subtractDecimalStrings,
} from "../utils/money";
import { formatTimestamp } from "../utils/datetime";
import {
  guardrailReasonFrom,
  guardrailThresholdFrom,
  humanise,
  isNonConclusive,
  recordedDecisionFrom,
  statusTone,
} from "../utils/workflow";
import { Badge } from "../components/Badge";
import { AuditTimeline } from "../components/AuditTimeline";
import { EvidenceList } from "../components/Evidence";
import { ReviewPanel } from "../components/ReviewPanel";
import { ErrorPanel, LoadingPanel, Notice } from "../components/StateViews";

/**
 * One investigation, end to end.
 *
 * Laid out to make the product's central separation legible: what the
 * deterministic engine *detected*, what the AI *proposed*, what the
 * deterministic guardrail *decided*, and what a human *authorised*. Each
 * section is tagged with its origin, because a reader who cannot tell an AI
 * conclusion from a financial fact is the failure mode this whole system exists
 * to avoid.
 */

interface CaseData {
  investigation: Investigation;
  recommendation: Recommendation | null;
  audit: AuditEvent[];
  exception: ReconciliationException | null;
  transaction: Transaction | null;
  settlements: Settlement[];
  /** The Financial Core was unreachable; investigation data is still valid. */
  coreUnavailable: boolean;
}

export function InvestigationDetailPage() {
  const { investigationId = "" } = useParams();
  const [data, setData] = useState<CaseData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // The Investigation Service is the source of truth for this page; if it
      // fails there is nothing to show.
      const investigation = await getInvestigation(investigationId);
      const [recommendation, auditList] = await Promise.all([
        getRecommendation(investigationId),
        getAuditTrail(investigationId),
      ]);

      // Financial Core data enriches the page but is not required for it. A
      // separate service being down should degrade one section, not the screen.
      let exception: ReconciliationException | null = null;
      let transaction: Transaction | null = null;
      let settlements: Settlement[] = [];
      let coreUnavailable = false;
      try {
        const [ex, tx, settlementList] = await Promise.all([
          getException(investigation.exception_id),
          getTransaction(investigation.transaction_id),
          getSettlements(investigation.transaction_id),
        ]);
        exception = ex;
        transaction = tx;
        settlements = settlementList?.settlements ?? [];
      } catch {
        coreUnavailable = true;
      }

      setData({
        investigation,
        recommendation,
        audit: auditList.items,
        exception,
        transaction,
        settlements,
        coreUnavailable,
      });
    } catch (caught) {
      setError(caught);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [investigationId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && data === null) {
    return (
      <div className="page">
        <BackLink />
        <LoadingPanel label={`Loading ${investigationId}`} />
      </div>
    );
  }

  if (error !== null) {
    const notFound = error instanceof ApiError && error.isNotFound;
    return (
      <div className="page">
        <BackLink />
        {notFound ? (
          <div className="state-panel">
            <div className="state-title">Investigation not found</div>
            <div className="state-detail">
              No investigation with identifier <span className="mono">{investigationId}</span>{" "}
              exists. Investigations are created only from reconciliation exception events.
            </div>
          </div>
        ) : (
          <ErrorPanel error={error} onRetry={() => void load()} />
        )}
      </div>
    );
  }

  if (data === null) return null;

  const { investigation, recommendation, audit, exception, transaction, settlements } = data;
  const threshold = guardrailThresholdFrom(audit);
  const guardrailReason = guardrailReasonFrom(audit);
  const decision = recordedDecisionFrom(audit);

  return (
    <div className="page">
      <BackLink />

      <CaseHeader investigation={investigation} />

      <Pipeline
        investigation={investigation}
        hasRecommendation={recommendation !== null}
        decided={decision !== null}
      />

      {data.coreUnavailable && (
        <Notice tone="warning" title="Financial Core unavailable">
          The authoritative financial records could not be loaded, so the discrepancy
          section is incomplete. Investigation, evidence and audit data below are
          unaffected.
        </Notice>
      )}

      {investigation.status === "RUNNING" && (
        <Notice tone="info" title="Investigation in progress">
          Evidence gathering is under way. Refresh to see the outcome.
        </Notice>
      )}

      {investigation.status === "FAILED" && (
        <Notice tone="error" title="Investigation failed">
          This investigation ended without a usable result — either the evidence did not
          support a conclusion, or a cited reference could not be verified. Nothing was
          stored, and no recommendation exists. See the audit trail below.
        </Notice>
      )}

      <FinancialDiscrepancy
        exception={exception}
        transaction={transaction}
        settlements={settlements}
      />

      {recommendation ? (
        <>
          <AiResult recommendation={recommendation} />
          <Guardrail
            investigation={investigation}
            recommendation={recommendation}
            threshold={threshold}
            reason={guardrailReason}
          />
          <section className="card">
            <div className="card-head">
              <h2 className="card-title">Evidence</h2>
              <span className="origin-tag deterministic">Verified</span>
              <span className="card-note">
                Every citation was checked against what the tools returned before storage
              </span>
            </div>
            <div className="card-body">
              <EvidenceList evidence={recommendation.evidence} />
            </div>
          </section>
        </>
      ) : (
        <section className="card">
          <div className="card-head">
            <h2 className="card-title">AI investigation</h2>
            <span className="origin-tag ai">AI-generated</span>
          </div>
          <div className="card-body">
            <p className="section-note" style={{ marginBottom: 0 }}>
              {investigation.status === "PENDING"
                ? "This investigation has not been run. No conclusion exists yet."
                : investigation.status === "RUNNING"
                  ? "The investigation is running. No conclusion has been stored yet."
                  : "No recommendation was stored for this investigation."}
            </p>
          </div>
        </section>
      )}

      <ReviewPanel
        investigation={investigation}
        recordedDecision={decision}
        onReviewed={load}
      />

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Audit trail</h2>
          <span className="card-note">Append-only · {audit.length} events</span>
        </div>
        <div className="card-body">
          <AuditTimeline events={audit} />
        </div>
      </section>
    </div>
  );
}

function BackLink() {
  return (
    <Link className="back-link" to="/investigations">
      ← Exception queue
    </Link>
  );
}

function CaseHeader({ investigation }: { investigation: Investigation }) {
  return (
    <div className="page-head">
      <h1 className="page-title">
        <span className="mono">{investigation.investigation_id}</span>{" "}
        <Badge tone={statusTone(investigation.status)} label={investigation.status} />
      </h1>
      <p className="page-subtitle">
        Investigating exception <span className="mono">{investigation.exception_id}</span> on
        transaction <span className="mono">{investigation.transaction_id}</span>
      </p>
      <div className="detail-grid" style={{ marginTop: 14 }}>
        <Item label="Detected exception">
          <Badge tone="neutral" label={investigation.exception_type} />
        </Item>
        <Item label="Detected at">{formatTimestamp(investigation.detected_at)}</Item>
        <Item label="Investigation opened">{formatTimestamp(investigation.created_at)}</Item>
        <Item label="Last updated">{formatTimestamp(investigation.updated_at)}</Item>
      </div>
    </div>
  );
}

/** The product claim, rendered as workflow position. */
function Pipeline({
  investigation,
  hasRecommendation,
  decided,
}: {
  investigation: Investigation;
  hasRecommendation: boolean;
  decided: boolean;
}) {
  const status = investigation.status;
  const steps = [
    {
      kind: "deterministic" as const,
      label: "Detection",
      value: humanise(investigation.exception_type),
      current: false,
    },
    {
      kind: "ai" as const,
      label: "AI investigation",
      value: hasRecommendation
        ? "Explanation proposed"
        : status === "RUNNING"
          ? "In progress"
          : status === "FAILED"
            ? "No usable result"
            : "Not run",
      current: status === "RUNNING",
    },
    {
      kind: "deterministic" as const,
      label: "Guardrail",
      value:
        status === "AWAITING_REVIEW"
          ? "Routed for review"
          : status === "ESCALATED"
            ? "Escalated"
            : hasRecommendation
              ? "Applied"
              : "Not reached",
      current: status === "ESCALATED",
    },
    {
      kind: "human" as const,
      label: "Human authority",
      value: decided
        ? "Decision recorded"
        : status === "AWAITING_REVIEW"
          ? "Awaiting decision"
          : "No decision",
      current: status === "AWAITING_REVIEW" || status === "COMPLETED",
    },
  ];

  return (
    <div className="pipeline">
      {steps.map((step) => (
        <div className={`pipeline-step${step.current ? " is-current" : ""}`} key={step.label}>
          <div className={`pipeline-kind ${step.kind}`}>
            {step.kind === "ai" ? "AI" : step.kind === "human" ? "Human" : "Deterministic"}
          </div>
          <div className="pipeline-label">{step.label}</div>
          <div className="pipeline-value">{step.value}</div>
        </div>
      ))}
    </div>
  );
}

function FinancialDiscrepancy({
  exception,
  transaction,
  settlements,
}: {
  exception: ReconciliationException | null;
  transaction: Transaction | null;
  settlements: Settlement[];
}) {
  // Prefer the backend's own decimal strings; it computed them with BigDecimal
  // against the authoritative records.
  const expected = exception?.expectedValue ?? null;
  const observed = exception?.observedValue ?? null;
  const difference =
    exception?.differenceAmount !== null && exception?.differenceAmount !== undefined
      ? numberToDecimalString(exception.differenceAmount)
      : expected && observed
        ? subtractDecimalStrings(expected, observed)
        : null;

  const settlement = settlements.find((s) => s.settlementId === exception?.settlementId) ?? settlements[0];

  return (
    <section className="card deterministic-origin">
      <div className="card-head">
        <h2 className="card-title">Financial discrepancy</h2>
        <span className="origin-tag deterministic">Deterministic fact</span>
        <span className="card-note">Established by reconciliation, not by the model</span>
      </div>
      <div className="card-body">
        {exception === null ? (
          <p className="section-note" style={{ marginBottom: 0 }}>
            The reconciliation exception could not be loaded from the Financial Core.
          </p>
        ) : (
          <>
            <div className="figures">
              <div className="figure-row">
                <span className="figure-label">Expected settlement</span>
                <span className="figure-amount">{formatMoney(expected, exception.currency)}</span>
              </div>
              <div className="figure-row">
                <span className="figure-label">Actual settlement</span>
                <span className="figure-amount">{formatMoney(observed, exception.currency)}</span>
              </div>
              <div className="figure-row total">
                <span className="figure-label">Difference</span>
                <span className="figure-amount">{formatMoney(difference, exception.currency)}</span>
              </div>
            </div>

            <div className="detail-grid" style={{ marginTop: 18 }}>
              <Item label="Exception type">
                <Badge tone="neutral" label={exception.exceptionType} />
              </Item>
              <Item label="Exception status">
                <Badge tone="neutral" label={exception.status} />
              </Item>
              {transaction && <Item label="Merchant"><span className="mono">{transaction.merchantId}</span></Item>}
              {settlement && <Item label="Processor"><span className="mono">{settlement.processor}</span></Item>}
              {settlement && (
                <Item label="Settlement">
                  <span className="mono">{settlement.settlementId}</span>{" "}
                  <Badge tone="neutral" label={settlement.status} />
                </Item>
              )}
              {settlements.length > 1 && (
                <Item label="Settlement count">{settlements.length}</Item>
              )}
            </div>

            <p className="caveat">
              The reconciliation exception remains <strong>{humanise(exception.status)}</strong> in
              the Financial Core. Reviewing an AI recommendation does not change it: this
              console issues no write to the Financial Core.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

function AiResult({ recommendation }: { recommendation: Recommendation }) {
  const nonConclusive = isNonConclusive(recommendation.classification);
  return (
    <section className="card ai-origin">
      <div className="card-head">
        <h2 className="card-title">AI investigation result</h2>
        <span className="origin-tag ai">AI-generated · advisory</span>
        <span className="card-note">A proposed explanation, not an established fact</span>
      </div>
      <div className="card-body stack">
        <div className="detail-grid">
          <Item label="Proposed root cause">
            <Badge tone={nonConclusive ? "warning" : "progress"} label={recommendation.classification} />
          </Item>
          <Item label="Requires human approval">
            {recommendation.requires_human_approval ? "Yes" : "No"}
          </Item>
          <Item label="Model">
            <span className="mono">
              {recommendation.model_provider ?? "—"} / {recommendation.model_name ?? "—"}
            </span>
          </Item>
          <Item label="Prompt version">
            <span className="mono">{recommendation.prompt_version ?? "—"}</span>
          </Item>
        </div>

        <div>
          <div className="detail-key">Root cause</div>
          <p className="prose" style={{ marginTop: 4 }}>
            {recommendation.root_cause}
          </p>
        </div>

        <div>
          <div className="detail-key">Recommended action</div>
          <p className="prose" style={{ marginTop: 4 }}>
            {recommendation.recommended_action}
          </p>
        </div>

        <p className="caveat">
          This classification is a proposal produced by a language model from the evidence
          below. It is not a determination of financial fact, and acting on it is a separate
          decision made by a person.
        </p>
      </div>
    </section>
  );
}

/**
 * The deterministic decision that follows the AI proposal.
 *
 * The threshold shown is read from the audit event that recorded the routing,
 * so it is the value actually applied rather than a constant duplicated here.
 */
function Guardrail({
  investigation,
  recommendation,
  threshold,
  reason,
}: {
  investigation: Investigation;
  recommendation: Recommendation;
  threshold: string | null;
  reason: string | null;
}) {
  const confidence = confidenceToPercent(recommendation.confidence);
  const thresholdPercent = threshold ? confidenceToPercent(threshold) : null;
  const below = thresholdPercent !== null && confidence < thresholdPercent;

  return (
    <section className="card deterministic-origin">
      <div className="card-head">
        <h2 className="card-title">Guardrail decision</h2>
        <span className="origin-tag deterministic">Deterministic policy</span>
        <span className="card-note">Application code decides routing, never the model</span>
      </div>
      <div className="card-body stack">
        <div className="confidence-row">
          <div>
            <div className="confidence-value">{formatConfidence(recommendation.confidence)}</div>
            <div className="detail-key">Model confidence</div>
          </div>
          <div className="confidence-track" role="img"
               aria-label={`Model confidence ${formatConfidence(recommendation.confidence)}${
                 threshold ? `, review threshold ${formatConfidence(threshold)}` : ""
               }`}>
            <div
              className={`confidence-fill${below ? " below" : ""}`}
              style={{ width: `${Math.min(100, Math.max(0, confidence))}%` }}
            />
            {thresholdPercent !== null && (
              <div
                className="confidence-threshold"
                style={{ left: `${thresholdPercent}%` }}
                data-label={`Threshold ${formatConfidence(threshold!)}`}
              />
            )}
          </div>
        </div>

        <div className="detail-grid">
          <Item label="Review threshold">
            {threshold ? formatConfidence(threshold) : "Not recorded"}
          </Item>
          <Item label="Routing outcome">
            <Badge tone={statusTone(investigation.status)} label={investigation.status} />
          </Item>
        </div>

        {reason && (
          <div>
            <div className="detail-key">Recorded reason</div>
            <p className="prose" style={{ marginTop: 4 }}>
              {reason}
            </p>
          </div>
        )}

        <p className="caveat">
          The model proposes; this policy decides whether a human sees the recommendation as
          a finding or the case is escalated. Escalation is a successful safety outcome, not
          an error — it is the system declining to present a weak explanation as a
          conclusion.
        </p>
      </div>
    </section>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="detail-item">
      <div className="detail-key">{label}</div>
      <div className="detail-value">{children}</div>
    </div>
  );
}
