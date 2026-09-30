import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvestigationDetailPage } from "./InvestigationDetailPage";

/**
 * The investigation detail page, across every status it can be in.
 *
 * This file exists because of INV-1004. The page rendered, at the same moment:
 *
 *     "This investigation has not been run. No conclusion exists yet."
 *     "Investigation INV-1004 returned a malformed result."
 *
 * The backend was consistent — FAILED, no recommendation, an INVESTIGATION_FAILED
 * audit event. The page was showing a status it had read before the run and an
 * error it had read after, with nothing forcing the two to agree. No test
 * covered this page at all, which is why it shipped.
 *
 * The regression assertion is `contradictory states are impossible`.
 */

const INVESTIGATION_ID = "INV-1004";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function investigation(status: string) {
  return {
    investigation_id: INVESTIGATION_ID,
    exception_id: "EX-2001",
    transaction_id: "TX-10043",
    exception_type: "AMOUNT_MISMATCH",
    status,
    detected_at: "2026-09-30T10:00:00Z",
    created_at: "2026-09-30T10:00:01Z",
    updated_at: "2026-09-30T10:00:05Z",
  };
}

function recommendation(classification = "PROCESSOR_FEE", confidence = "0.7200") {
  return {
    recommendation_id: "REC-3001",
    investigation_id: INVESTIGATION_ID,
    classification,
    root_cause: "A processing fee matches the settlement difference.",
    confidence,
    confidence_note: "Self-reported by the model; not a calibrated probability.",
    recommended_action: "Classify as a processor fee adjustment.",
    requires_human_approval: true,
    model_provider: "anthropic",
    model_name: "claude-sonnet-5",
    prompt_version: "v1",
    created_at: "2026-09-30T10:00:04Z",
    evidence: [
      { source_type: "FEE_RULE", reference: "FR-14", section: null, excerpt: null },
    ],
  };
}

function auditEvent(eventType: string, actorType: string, metadata: unknown = null) {
  return {
    event_id: `AUD-${eventType}`,
    investigation_id: INVESTIGATION_ID,
    event_type: eventType,
    actor_type: actorType,
    actor_id: null,
    metadata,
    occurred_at: "2026-09-30T10:00:02Z",
  };
}

/** Routes every request the page makes, so one stub serves both services. */
function stubBackend({
  status,
  rec = null,
  audit = [],
}: {
  status: string;
  rec?: unknown;
  audit?: unknown[];
}) {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/recommendation")) {
      return rec === null
        ? jsonResponse(404, { detail: "no recommendation yet" })
        : jsonResponse(200, rec);
    }
    if (url.includes("/audit")) {
      return jsonResponse(200, {
        investigation_id: INVESTIGATION_ID,
        items: audit,
        total: audit.length,
      });
    }
    if (url.includes("/investigations/")) {
      return jsonResponse(200, investigation(status));
    }
    // Financial Core enrichment. Absent is a supported, degraded state.
    return jsonResponse(404, { message: "not found" });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage(refreshIntervalMs = 100_000) {
  return render(
    <MemoryRouter initialEntries={[`/investigations/${INVESTIGATION_ID}`]}>
      <Routes>
        <Route
          path="/investigations/:investigationId"
          element={<InvestigationDetailPage refreshIntervalMs={refreshIntervalMs} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

/**
 * Waits for text that may appear in more than one node.
 *
 * A notice renders its message inside a wrapper that also matches, and a
 * classification appears both as a badge and in a detail row. Requiring exactly
 * one match would assert page structure rather than page content.
 */
async function findText(pattern: RegExp | string) {
  const matches = await screen.findAllByText(pattern);
  expect(matches.length).toBeGreaterThan(0);
  return matches[0];
}

function queryText(pattern: RegExp | string) {
  return screen.queryAllByText(pattern);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// The regression
// ---------------------------------------------------------------------------

describe("contradictory states are impossible", () => {
  it("never says both 'has not been run' and 'it ran and failed'", async () => {
    stubBackend({
      status: "FAILED",
      audit: [
        auditEvent("INVESTIGATION_CREATED", "SYSTEM"),
        auditEvent("INVESTIGATION_STARTED", "SYSTEM"),
        auditEvent("INVESTIGATION_FAILED", "SYSTEM", {
          failure_type: "InvestigationFailed",
          detail: "Investigation INV-1004 returned a malformed result",
        }),
      ],
    });
    renderPage();

    await findText(/Investigation failed/i);

    // The exact pairing that shipped.
    expect(queryText(/has not been run/i)).toHaveLength(0);
    expect(queryText(/Queued for automatic investigation/i)).toHaveLength(0);
  });

  it("never offers a queued message on a terminal investigation", async () => {
    stubBackend({ status: "ESCALATED", rec: recommendation() });
    renderPage();

    await findText(/Processor Fee/i);

    expect(queryText(/Investigation queued/i)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Every status
// ---------------------------------------------------------------------------

describe("PENDING", () => {
  it("says the investigation is queued for automatic investigation", async () => {
    stubBackend({ status: "PENDING", audit: [auditEvent("INVESTIGATION_CREATED", "SYSTEM")] });
    renderPage();

    expect(await findText("Investigation queued")).toBeInTheDocument();
    expect(queryText(/queued for automatic investigation/i).length).toBeGreaterThan(0);
  });

  it("says only that it is queued when nothing paused it", async () => {
    stubBackend({ status: "PENDING", audit: [auditEvent("INVESTIGATION_CREATED", "SYSTEM")] });
    renderPage();

    expect(await findText(/queued for automatic investigation/i)).toBeInTheDocument();
    // No cause is claimed, because none was recorded.
    expect(queryText(/AI budget/i)).toHaveLength(0);
    expect(queryText(/provider could not be reached/i)).toHaveLength(0);
  });

  it("names an exhausted AI budget when the audit trail recorded one", async () => {
    stubBackend({
      status: "PENDING",
      audit: [
        auditEvent("INVESTIGATION_CREATED", "SYSTEM"),
        auditEvent("INVESTIGATION_AUTO_RUN_PAUSED", "SYSTEM", {
          reason: "AI_BUDGET_EXHAUSTED",
          retry_after_seconds: 1800,
        }),
      ],
    });
    renderPage();

    expect(
      await findText(/temporarily unavailable because the demo AI budget is exhausted/i),
    ).toBeInTheDocument();
    // Never promises it will resume on its own, because it will not.
    expect(queryText(/until the window resets/i)).toHaveLength(0);
    expect(queryText(/automatically resume/i)).toHaveLength(0);
  });

  it("names an unreachable provider when that is what was recorded", async () => {
    stubBackend({
      status: "PENDING",
      audit: [
        auditEvent("INVESTIGATION_CREATED", "SYSTEM"),
        auditEvent("INVESTIGATION_AUTO_RUN_PAUSED", "SYSTEM", {
          reason: "PROVIDER_UNAVAILABLE",
          attempts: 2,
        }),
      ],
    });
    renderPage();

    expect(await findText(/model provider could not be reached/i)).toBeInTheDocument();
    expect(queryText(/AI budget/i)).toHaveLength(0);
  });

  it("does not infer a pause from a long-standing PENDING investigation", async () => {
    // Elapsed time says nothing about the cause. Only the trail does.
    stubBackend({
      status: "PENDING",
      audit: [auditEvent("INVESTIGATION_RETRY_SCHEDULED", "SYSTEM", { attempt: 1 })],
    });
    renderPage();

    await findText(/queued for automatic investigation/i);
    expect(queryText(/AI budget/i)).toHaveLength(0);
  });

  it("offers no recovery control, even when paused", async () => {
    // The operator run endpoint is not a public UI action.
    stubBackend({
      status: "PENDING",
      audit: [
        auditEvent("INVESTIGATION_AUTO_RUN_PAUSED", "SYSTEM", {
          reason: "AI_BUDGET_EXHAUSTED",
        }),
      ],
    });
    renderPage();

    await findText(/demo AI budget is exhausted/i);
    expect(screen.queryByRole("button", { name: /run/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("offers no Run AI Investigation button", async () => {
    stubBackend({ status: "PENDING" });
    renderPage();

    await findText("Investigation queued");
    expect(
      screen.queryByRole("button", { name: /run ai investigation/i }),
    ).not.toBeInTheDocument();
  });
});

describe("RUNNING", () => {
  it("says the agent is using controlled read-only tools", async () => {
    stubBackend({ status: "RUNNING" });
    renderPage();

    expect(await findText("Investigation in progress")).toBeInTheDocument();
    expect(queryText(/controlled read-only tools/i).length).toBeGreaterThan(0);
  });

  it("offers no run button while one is already in flight", async () => {
    stubBackend({ status: "RUNNING" });
    renderPage();

    await findText("Investigation in progress");
    expect(
      screen.queryByRole("button", { name: /run ai investigation/i }),
    ).not.toBeInTheDocument();
  });
});

describe("AWAITING_REVIEW", () => {
  it("shows the recommendation and the human review controls", async () => {
    stubBackend({
      status: "AWAITING_REVIEW",
      rec: recommendation(),
      audit: [
        auditEvent("INVESTIGATION_AWAITING_REVIEW", "SYSTEM", {
          reason: "Reported confidence 0.9200 meets the review threshold 0.85.",
        }),
      ],
    });
    renderPage();

    expect(await findText(/Processor Fee/i)).toBeInTheDocument();
    expect(queryText(/FR-14/).length).toBeGreaterThan(0);
    expect(queryText(/meets the review threshold/i).length).toBeGreaterThan(0);
  });
});

describe("ESCALATED", () => {
  it("shows the recommendation and the guardrail's recorded reason", async () => {
    stubBackend({
      status: "ESCALATED",
      rec: recommendation(),
      audit: [
        auditEvent("INVESTIGATION_ESCALATED", "SYSTEM", {
          reason: "Reported confidence 0.7200 is below the review threshold 0.85.",
        }),
      ],
    });
    renderPage();

    expect(await findText(/below the review threshold/i)).toBeInTheDocument();
    expect(queryText(/Processor Fee/i).length).toBeGreaterThan(0);
  });
});

describe("COMPLETED", () => {
  it("shows the recommendation and the recorded human decision", async () => {
    stubBackend({
      status: "COMPLETED",
      rec: recommendation(),
      audit: [
        auditEvent("REVIEW_APPROVED", "HUMAN", {
          decision: "APPROVED",
          resulting_status: "COMPLETED",
        }),
      ],
    });
    renderPage();

    expect(await findText(/Processor Fee/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Completed/i).length).toBeGreaterThan(0);
  });
});

describe("FAILED", () => {
  it("shows the authoritative reason from the audit event", async () => {
    stubBackend({
      status: "FAILED",
      audit: [
        auditEvent("INVESTIGATION_FAILED", "SYSTEM", {
          failure_type: "InvestigationFailed",
          detail: "Investigation INV-1004 returned a malformed result",
        }),
      ],
    });
    renderPage();

    expect(await findText(/returned a malformed result/i)).toBeInTheDocument();
  });

  it("distinguishes an ungrounded citation from a malformed result", async () => {
    // The page used to assert one hardcoded cause for every failure.
    stubBackend({
      status: "FAILED",
      audit: [
        auditEvent("INVESTIGATION_FAILED", "SYSTEM", {
          failure_type: "UngroundedResultError",
          detail: "cited evidence that was never retrieved: FEE_RULE/FR-999",
        }),
      ],
    });
    renderPage();

    expect(await findText(/never retrieved/i)).toBeInTheDocument();
    expect(queryText(/malformed/i)).toHaveLength(0);
  });

  it("falls back to a generic sentence when no reason was recorded", async () => {
    stubBackend({ status: "FAILED", audit: [] });
    renderPage();

    expect(await findText(/ended without a usable result/i)).toBeInTheDocument();
  });

  it("states that no recommendation was stored", async () => {
    stubBackend({ status: "FAILED", audit: [] });
    renderPage();

    await findText(/Investigation failed/i);
    expect(screen.getByText(/no recommendation exists/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Watching an investigation that is still moving
// ---------------------------------------------------------------------------

describe("auto-refresh", () => {
  it("keeps re-reading while the investigation is unsettled", async () => {
    const fetchMock = stubBackend({ status: "RUNNING" });
    renderPage(20);

    await findText("Investigation in progress");
    const early = fetchMock.mock.calls.length;

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(early));
  });

  it("stops once the investigation has settled", async () => {
    const fetchMock = stubBackend({ status: "ESCALATED", rec: recommendation() });
    renderPage(20);

    await findText(/Processor Fee/i);
    const settled = fetchMock.mock.calls.length;

    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(fetchMock.mock.calls.length).toBe(settled);
  });
});
