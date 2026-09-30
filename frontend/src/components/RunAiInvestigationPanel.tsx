import { useState } from "react";

import { ApiError, NetworkError } from "../api/client";
import type { RunResult } from "../types";

/**
 * The explicit step that starts an AI investigation.
 *
 * Separate on purpose, and visibly so. Detecting a discrepancy is
 * deterministic, free and automatic; explaining one calls a language model and
 * costs money. Nothing in ReconAI crosses that line implicitly — not
 * reconciliation, not Kafka consumption, not opening this page. A person
 * decides, and this button is that decision.
 *
 * On success the panel does not render the result. The page reloads and the
 * existing recommendation, guardrail, evidence and audit sections render it, so
 * there is exactly one place in the console where an AI conclusion is shown.
 */

type State =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "failed"; error: unknown };

export function RunAiInvestigationPanel({
  investigationId,
  onRun,
  onCompleted,
}: {
  investigationId: string;
  onRun: () => Promise<RunResult>;
  onCompleted: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });

  const run = async () => {
    setState({ kind: "running" });
    try {
      await onRun();
      // Deliberately not storing the result. Reloading makes the page render it
      // through the same components a pre-existing result goes through, so a
      // freshly run investigation and an old one look identical.
      onCompleted();
      setState({ kind: "idle" });
    } catch (error) {
      setState({ kind: "failed", error });
    }
  };

  const running = state.kind === "running";

  return (
    <div className="run-ai">
      <div className="run-ai-head">
        <div>
          <div className="run-ai-title">Separate step — not part of reconciliation</div>
          <p className="caveat" style={{ marginBottom: 0 }}>
            The discrepancy above was established deterministically by the Financial
            Core. Running an investigation asks a language model to propose a
            <em> root cause</em> from controlled evidence. Its conclusion is advisory,
            is checked against the evidence actually retrieved, and still requires a
            human decision.
          </p>
        </div>
        <button
          type="button"
          className="btn primary"
          onClick={() => void run()}
          disabled={running}
          aria-describedby={`run-ai-note-${investigationId}`}
        >
          {running ? "Investigating…" : "Run AI Investigation"}
        </button>
      </div>

      {running && (
        <p className="caveat" id={`run-ai-note-${investigationId}`} role="status">
          Gathering evidence through the four read-only tools and validating every
          citation. This takes a few seconds.
        </p>
      )}

      {state.kind === "failed" && <RunFailure error={state.error} />}
    </div>
  );
}

function RunFailure({ error }: { error: unknown }) {
  if (error instanceof ApiError && error.status === 429) {
    return (
      <div className="notice warning" role="alert">
        <div className="notice-title">AI run limit reached</div>
        <p style={{ marginBottom: 0 }}>
          {error.detail} Running an investigation calls a paid model, so this public
          demo caps how often it can happen.
        </p>
      </div>
    );
  }

  if (error instanceof ApiError && error.status === 503) {
    return (
      <div className="notice warning" role="alert">
        <div className="notice-title">No model configured</div>
        <p style={{ marginBottom: 0 }}>
          {error.detail} Deterministic reconciliation is unaffected — the discrepancy
          above was detected without any model.
        </p>
      </div>
    );
  }

  if (error instanceof ApiError && error.status === 409) {
    return (
      <div className="notice info" role="alert">
        <div className="notice-title">Already run</div>
        <p style={{ marginBottom: 0 }}>
          {error.detail} Refresh to see the stored outcome.
        </p>
      </div>
    );
  }

  if (error instanceof ApiError && error.status === 422) {
    // A genuine outcome, not a malfunction: the evidence did not support a
    // conclusion, or a citation could not be verified, so nothing was stored.
    return (
      <div className="notice warning" role="alert">
        <div className="notice-title">Investigation produced no usable result</div>
        <p style={{ marginBottom: 0 }}>
          {error.detail} No recommendation was stored — an unsupported conclusion is
          discarded rather than shown.
        </p>
      </div>
    );
  }

  if (error instanceof NetworkError) {
    return (
      <div className="notice error" role="alert">
        <div className="notice-title">Could not reach the Investigation Service</div>
        <p style={{ marginBottom: 0 }}>{error.message}</p>
      </div>
    );
  }

  return (
    <div className="notice error" role="alert">
      <div className="notice-title">The investigation could not be run</div>
      <p style={{ marginBottom: 0 }}>
        {error instanceof Error ? error.message : String(error)}
      </p>
    </div>
  );
}
