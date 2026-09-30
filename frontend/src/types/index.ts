/**
 * Types mirroring the backends' actual response shapes.
 *
 * Two services, two owners, and the split is deliberate: the Spring Financial
 * Core is authoritative for transactions, settlements and detected exceptions;
 * the Python Investigation Service owns investigations, recommendations,
 * reviews and audit. Nothing here invents a field neither service returns.
 *
 * Monetary values arrive as decimal **strings** wherever the backend sends them
 * that way, and stay strings. Parsing "2500.00" into a JS number to render it
 * would introduce binary floating point into a financial display for no reason.
 */

// ---------------------------------------------------------------------------
// Financial Core (Spring) — authoritative financial records
// ---------------------------------------------------------------------------

/** Deterministic discrepancy types. Never a root cause. */
export type ExceptionType =
  | "AMOUNT_MISMATCH"
  | "MISSING_SETTLEMENT"
  | "DUPLICATE_SETTLEMENT"
  | "CURRENCY_MISMATCH";

export type ExceptionStatus =
  | "OPEN"
  | "INVESTIGATING"
  | "AWAITING_REVIEW"
  | "RESOLVED"
  | "ESCALATED";

export interface ReconciliationException {
  exceptionId: string;
  transactionId: string;
  settlementId: string | null;
  exceptionType: ExceptionType;
  /** Decimal string, e.g. "2500.00". */
  expectedValue: string | null;
  /** Decimal string, e.g. "2450.00". */
  observedValue: string | null;
  /** Serialised by Jackson as a JSON number; rendered, never recomputed. */
  differenceAmount: number | null;
  currency: string | null;
  status: ExceptionStatus;
  detectedAt: string;
}

export interface Transaction {
  transactionId: string;
  merchantId: string;
  amount: number;
  expectedSettlementAmount: number;
  currency: string;
  transactionType: string;
  status: string;
  transactionTimestamp: string;
  createdAt: string;
}

export interface Settlement {
  settlementId: string;
  transactionId: string;
  processor: string;
  settledAmount: number;
  currency: string;
  status: string;
  settlementTimestamp: string;
}

export interface TransactionSettlements {
  transactionId: string;
  settlements: Settlement[];
}

// ---------------------------------------------------------------------------
// Demo reconciliation playground (Financial Core)
// ---------------------------------------------------------------------------

/**
 * What the console sends for a synthetic run.
 *
 * Amounts are strings, not numbers, so a decimal typed by hand survives the
 * round trip exactly. `2500.00` as a JSON number is a double before it is ever
 * a BigDecimal; as a string it is parsed once, by the server, at full precision.
 *
 * Only these fields exist. Identity, merchant, processor, statuses, types and
 * timestamps are the server's, and a value supplied for any of them is ignored.
 */
export interface DemoTransactionInput {
  amount: string;
  expectedSettlementAmount: string;
  currency: string;
}

export interface DemoSettlementInput {
  settledAmount: string;
  currency: string;
}

/** An empty `settlements` array is how MISSING_SETTLEMENT is expressed. */
export interface DemoReconcileRequest {
  transaction: DemoTransactionInput;
  settlements: DemoSettlementInput[];
}

/** One discrepancy, exactly as the deterministic engine reported it. */
export interface ReconciledException {
  exceptionId: string;
  exceptionType: ExceptionType;
  expectedValue: string | null;
  observedValue: string | null;
  differenceAmount: number | null;
  currency: string | null;
  status: ExceptionStatus;
}

export interface ReconciliationResult {
  transactionId: string;
  reconciled: boolean;
  exceptions: ReconciledException[];
  reconciledAt: string;
}

export interface DemoReconcileResponse {
  transactionId: string;
  settlementIds: string[];
  reconciliation: ReconciliationResult;
}

// ---------------------------------------------------------------------------
// Investigation Service (Python) — the AI lifecycle
// ---------------------------------------------------------------------------

export type InvestigationStatus =
  | "PENDING"
  | "RUNNING"
  | "AWAITING_REVIEW"
  | "COMPLETED"
  | "FAILED"
  | "ESCALATED";

/**
 * Root-cause taxonomy. Deliberately disjoint from ExceptionType: reconciliation
 * says two records disagree, an investigation may say why. PROCESSOR_FEE
 * appears here and never there.
 */
export type RootCauseClassification =
  | "PROCESSOR_FEE"
  | "PROCESSOR_DELAY"
  | "DUPLICATE_PROCESSING"
  | "CURRENCY_CONVERSION"
  | "PROCESSOR_ERROR"
  | "UNKNOWN"
  | "INSUFFICIENT_EVIDENCE";

export interface Investigation {
  investigation_id: string;
  exception_id: string;
  transaction_id: string;
  exception_type: ExceptionType;
  status: InvestigationStatus;
  detected_at: string;
  created_at: string;
  updated_at: string;
}

export interface InvestigationList {
  items: Investigation[];
  total: number;
}

export type EvidenceSourceType =
  | "TRANSACTION"
  | "SETTLEMENT"
  | "FEE_RULE"
  | "POLICY_DOCUMENT";

export interface Evidence {
  source_type: EvidenceSourceType;
  reference: string;
  /** Populated for policy evidence only. */
  section: string | null;
  /** The retrieved text, stored for policy evidence only. */
  excerpt: string | null;
}

export interface Recommendation {
  recommendation_id: string;
  investigation_id: string;
  classification: RootCauseClassification;
  root_cause: string;
  /** Decimal string, e.g. "0.7000". Self-reported, not calibrated. */
  confidence: string;
  confidence_note: string;
  recommended_action: string;
  requires_human_approval: boolean;
  model_provider: string | null;
  model_name: string | null;
  prompt_version: string | null;
  created_at: string;
  evidence: Evidence[];
}

/**
 * The result of running an AI investigation.
 *
 * `guardrail_reason` is written by deterministic application code, not by the
 * model, which is why it can be shown as an explanation of the routing rather
 * than as another model claim.
 */
export interface RunResult {
  investigation_id: string;
  status: InvestigationStatus;
  guardrail_reason: string;
  recommendation: Recommendation;
  evidence_retrieved: Record<string, string[]>;
}

export type ReviewDecision = "APPROVED" | "REJECTED" | "ESCALATED";

export interface Review {
  review_id: string;
  investigation_id: string;
  recommendation_id: string;
  decision: ReviewDecision;
  reviewed_by: string;
  /** The backend's own statement that this identity is unverified. */
  reviewer_note: string;
  comment: string | null;
  decided_at: string;
}

export type ActorType = "SYSTEM" | "AI" | "HUMAN";

export type AuditEventType =
  | "INVESTIGATION_CREATED"
  | "INVESTIGATION_STARTED"
  | "AI_RESULT_GENERATED"
  | "INVESTIGATION_AWAITING_REVIEW"
  | "INVESTIGATION_ESCALATED"
  | "INVESTIGATION_FAILED"
  | "REVIEW_APPROVED"
  | "REVIEW_REJECTED";

export interface AuditEvent {
  event_id: string;
  investigation_id: string;
  event_type: AuditEventType;
  actor_type: ActorType;
  actor_id: string | null;
  /**
   * A small non-sensitive summary written by the backend. Never a prompt,
   * provider payload, or model reasoning — the service does not persist those,
   * so there is nothing here to leak.
   */
  metadata: Record<string, unknown> | null;
  occurred_at: string;
}

export interface AuditList {
  investigation_id: string;
  items: AuditEvent[];
  total: number;
}
