import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, NetworkError } from "../api/client";
import { runDemoReconciliation } from "../api/demo";
import { findInvestigationByExceptionId } from "../api/investigations";
import {
  DEFAULT_SCENARIO_ID,
  SCENARIOS,
  scenarioJson,
  type DemoScenario,
} from "../demo/scenarios";
import type { DemoReconcileResponse, ReconciledException } from "../types";
import { formatMoney } from "../utils/money";
import { Badge } from "./Badge";

/**
 * The reconciliation playground.
 *
 * INPUT -> DETERMINISTIC RECONCILIATION -> RESULT, and nothing else. It shows no
 * AI result, no evidence, no guardrail and no review controls: those belong to
 * the investigation detail page, which already renders them, and duplicating
 * them here would blur the distinction the whole product is about.
 *
 * When a run produces a discrepancy the modal waits for the investigation to
 * appear — the exception travels to the Investigation Service over Kafka, so it
 * is not there the instant reconciliation returns — and then offers a link. It
 * does not start the investigation. Running the AI is a separate, explicit
 * action taken on the detail page.
 */

/**
 * How the async investigation lookup is bounded.
 *
 * Overridable as props so tests can exercise the timeout and the retry loop at
 * millisecond scale. Faking timers here does not work: the loop awaits real
 * promises between ticks, and a mocked clock deadlocks against them.
 */
const POLL_INTERVAL_MS = 1000;
const POLL_TIMEOUT_MS = 15000;

type Phase =
  | { kind: "editing" }
  | { kind: "running" }
  | { kind: "result"; response: DemoReconcileResponse }
  | { kind: "failed"; error: unknown };

/** Where the investigation lookup has got to, for an exception result. */
type Discovery =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "found"; investigationId: string }
  | { kind: "timedOut" };

export function RunReconciliationModal({
  onClose,
  pollIntervalMs = POLL_INTERVAL_MS,
  pollTimeoutMs = POLL_TIMEOUT_MS,
}: {
  onClose: () => void;
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
}) {
  const [scenarioId, setScenarioId] = useState(DEFAULT_SCENARIO_ID);
  const [json, setJson] = useState(() => scenarioJson(scenarioFor(DEFAULT_SCENARIO_ID)));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "editing" });
  const [discovery, setDiscovery] = useState<Discovery>({ kind: "idle" });

  // Guards the poll loop against a modal that closes mid-flight, which would
  // otherwise set state on an unmounted component.
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const selectScenario = (scenario: DemoScenario) => {
    // Selecting a scenario only fills the editor. It is not a mode, and the
    // server is never told which template a payload came from.
    setScenarioId(scenario.id);
    setJson(scenarioJson(scenario));
    setJsonError(null);
    setPhase({ kind: "editing" });
    setDiscovery({ kind: "idle" });
  };

  const discoverInvestigation = useCallback(
    async (exceptionId: string) => {
      setDiscovery({ kind: "searching" });
      const deadline = Date.now() + pollTimeoutMs;

      while (Date.now() < deadline) {
        if (!active.current) return;
        try {
          const investigation = await findInvestigationByExceptionId(exceptionId);
          if (investigation !== null) {
            if (active.current) {
              setDiscovery({ kind: "found", investigationId: investigation.investigation_id });
            }
            return;
          }
        } catch {
          // A failed lookup is not a failed run. The exception is recorded
          // either way, so keep trying until the deadline rather than
          // reporting an error about a record that exists.
        }
        await sleep(pollIntervalMs);
      }

      // Bounded, never indefinite: a lost Kafka event would otherwise spin
      // here forever. The exception ID is still shown, so the run is not lost.
      if (active.current) setDiscovery({ kind: "timedOut" });
    },
    [pollIntervalMs, pollTimeoutMs],
  );

  const submit = async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(json);
    } catch (error) {
      setJsonError(
        error instanceof Error
          ? `That is not valid JSON — ${error.message}`
          : "That is not valid JSON.",
      );
      return;
    }

    setJsonError(null);
    setPhase({ kind: "running" });
    setDiscovery({ kind: "idle" });

    try {
      // Sent as parsed, not as a reshaped object: a hand-edited payload should
      // reach the server as written so its validation is what reports problems.
      const response = await runDemoReconciliation(
        parsed as Parameters<typeof runDemoReconciliation>[0],
      );
      if (!active.current) return;
      setPhase({ kind: "result", response });

      const exception = response.reconciliation.exceptions[0];
      if (exception) {
        void discoverInvestigation(exception.exceptionId);
      }
    } catch (error) {
      if (active.current) setPhase({ kind: "failed", error });
    }
  };

  const busy = phase.kind === "running";

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div
        className="dialog dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-reconciliation-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="dialog-head">
          <h2 className="dialog-title" id="run-reconciliation-title">
            Run reconciliation
          </h2>
          <p className="field-hint" style={{ marginBottom: 0 }}>
            Test the deterministic reconciliation engine using synthetic transaction data.
          </p>
        </div>

        <div className="dialog-body">
          <div className="field">
            <label>Example scenarios</label>
            <div className="scenario-chips">
              {SCENARIOS.map((scenario) => (
                <button
                  key={scenario.id}
                  type="button"
                  className={`chip${scenario.id === scenarioId ? " is-active" : ""}`}
                  onClick={() => selectScenario(scenario)}
                  disabled={busy}
                >
                  {scenario.label}
                </button>
              ))}
            </div>
            <p className="field-hint">{scenarioFor(scenarioId).expectation}</p>
          </div>

          <div className="field">
            <label htmlFor="demo-payload">
              Transaction and settlements
            </label>
            <textarea
              id="demo-payload"
              className="code-editor"
              value={json}
              spellCheck={false}
              rows={14}
              disabled={busy}
              onChange={(event) => {
                setJson(event.target.value);
                setJsonError(null);
              }}
            />
            <p className="field-hint">
              Editable. Amounts and currencies are yours; identifiers, merchant,
              processor, statuses and timestamps are set by the server. An empty{" "}
              <code>settlements</code> list means nothing settled.
            </p>
            {jsonError !== null && (
              <p className="field-error" role="alert">
                {jsonError}
              </p>
            )}
          </div>

          {phase.kind === "failed" && <FailurePanel error={phase.error} />}

          {phase.kind === "result" && (
            <ResultPanel response={phase.response} discovery={discovery} />
          )}
        </div>

        <div className="dialog-foot">
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>
            {phase.kind === "result" ? "Done" : "Cancel"}
          </button>
          <button type="button" className="btn primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Reconciling…" : "Run reconciliation"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result
// ---------------------------------------------------------------------------

function ResultPanel({
  response,
  discovery,
}: {
  response: DemoReconcileResponse;
  discovery: Discovery;
}) {
  const { reconciliation, transactionId } = response;
  const exception = reconciliation.exceptions[0];

  if (reconciliation.reconciled || exception === undefined) {
    return (
      <div className="notice positive" role="status">
        <div className="notice-title">Reconciled successfully</div>
        <p style={{ marginBottom: 8 }}>
          No discrepancy detected. No AI investigation required.
        </p>
        <MatchFigures response={response} />
        <p className="caveat" style={{ marginBottom: 0 }}>
          Transaction <span className="mono">{transactionId}</span>
        </p>
      </div>
    );
  }

  return (
    <div className="notice warning" role="status">
      <div className="notice-title">
        Exception detected <Badge tone="warning" label={exception.exceptionType} />
      </div>

      <ExceptionFigures exception={exception} />

      <div className="detail-grid tight">
        <div className="detail-item">
          <div className="detail-key">Transaction</div>
          <div className="detail-value mono">{transactionId}</div>
        </div>
        <div className="detail-item">
          <div className="detail-key">Exception</div>
          <div className="detail-value mono">{exception.exceptionId}</div>
        </div>
      </div>

      <DiscoveryStatus discovery={discovery} exceptionId={exception.exceptionId} />
    </div>
  );
}

function MatchFigures({ response }: { response: DemoReconcileResponse }) {
  // Only values the deterministic engine actually reported. A clean
  // reconciliation produces no exception row, so there is nothing to show
  // beyond the settlement count and the identifiers.
  const settled = response.settlementIds.length;
  return (
    <div className="detail-grid tight">
      <div className="detail-item">
        <div className="detail-key">Settlements created</div>
        <div className="detail-value">{settled}</div>
      </div>
      <div className="detail-item">
        <div className="detail-key">Reconciled at</div>
        <div className="detail-value">
          {new Date(response.reconciliation.reconciledAt).toLocaleTimeString()}
        </div>
      </div>
    </div>
  );
}

function ExceptionFigures({ exception }: { exception: ReconciledException }) {
  const isMonetary =
    exception.exceptionType === "AMOUNT_MISMATCH" && exception.differenceAmount !== null;

  return (
    <div className="figures">
      <div className="figure-row">
        <span className="figure-label">Expected</span>
        <span className="figure-amount mono">
          {formatValue(exception.expectedValue, exception.currency, isMonetary)}
        </span>
      </div>
      <div className="figure-row">
        <span className="figure-label">Observed</span>
        <span className="figure-amount mono">
          {formatValue(exception.observedValue, exception.currency, isMonetary)}
        </span>
      </div>
      {/* Only where the domain supplies one: a missing or duplicate settlement
          has no numeric difference, and inventing a zero would be a claim. */}
      {exception.differenceAmount !== null && (
        <div className="figure-row">
          <span className="figure-label">Difference</span>
          <span className="figure-amount mono">
            {formatMoney(exception.differenceAmount, exception.currency)}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * Renders expectedValue/observedValue, which are strings carrying either an
 * amount or a token like NO_SETTLEMENT depending on the exception type.
 */
function formatValue(value: string | null, currency: string | null, monetary: boolean): string {
  if (value === null) return "—";
  // Non-monetary types carry a token (NO_SETTLEMENT, a currency code, a list of
  // settlement IDs) rather than a number, and must be shown verbatim.
  return monetary ? formatMoney(value, currency) : value;
}

function DiscoveryStatus({
  discovery,
  exceptionId,
}: {
  discovery: Discovery;
  exceptionId: string;
}) {
  if (discovery.kind === "found") {
    return (
      <p className="discovery" style={{ marginBottom: 0 }}>
        <Link className="btn" to={`/investigations/${discovery.investigationId}`}>
          View investigation →
        </Link>
      </p>
    );
  }

  if (discovery.kind === "timedOut") {
    return (
      <p className="discovery caveat" style={{ marginBottom: 0 }} role="status">
        The investigation for <span className="mono">{exceptionId}</span> has not appeared
        yet. The exception is recorded and can be found from the exception queue.
      </p>
    );
  }

  return (
    <p className="discovery caveat" style={{ marginBottom: 0 }} role="status">
      Creating investigation…
    </p>
  );
}

function FailurePanel({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 429) {
    return (
      <div className="notice error" role="alert">
        <div className="notice-title">Too many runs</div>
        <p style={{ marginBottom: 0 }}>{error.detail}</p>
      </div>
    );
  }

  if (error instanceof ApiError && error.status === 413) {
    return (
      <div className="notice error" role="alert">
        <div className="notice-title">Payload too large</div>
        <p style={{ marginBottom: 0 }}>{error.detail}</p>
      </div>
    );
  }

  if (error instanceof ApiError) {
    return (
      <div className="notice error" role="alert">
        <div className="notice-title">The engine rejected that input</div>
        <p style={{ marginBottom: 0 }}>{error.detail}</p>
      </div>
    );
  }

  if (error instanceof NetworkError) {
    return (
      <div className="notice error" role="alert">
        <div className="notice-title">Could not reach the Financial Core</div>
        <p style={{ marginBottom: 0 }}>{error.message}</p>
      </div>
    );
  }

  return (
    <div className="notice error" role="alert">
      <div className="notice-title">Something went wrong</div>
      <p style={{ marginBottom: 0 }}>{String(error)}</p>
    </div>
  );
}

function scenarioFor(id: string): DemoScenario {
  return SCENARIOS.find((scenario) => scenario.id === id) ?? SCENARIOS[0];
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
