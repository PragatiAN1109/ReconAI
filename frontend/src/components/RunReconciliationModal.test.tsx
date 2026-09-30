import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunReconciliationModal } from "./RunReconciliationModal";

/**
 * The reconciliation playground.
 *
 * Two properties matter most here. The modal must never present a deterministic
 * value it was not given — an invented difference or a fabricated exception
 * would undermine the one thing this screen exists to demonstrate. And the
 * asynchronous investigation lookup must always terminate: a lost Kafka event
 * has to end in a readable state, not a permanent spinner.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function matchResponse() {
  return {
    transactionId: "TX-10042",
    settlementIds: ["SET-5001"],
    reconciliation: {
      transactionId: "TX-10042",
      reconciled: true,
      exceptions: [],
      reconciledAt: "2026-09-30T10:00:00Z",
    },
  };
}

function exceptionResponse(overrides: Record<string, unknown> = {}) {
  return {
    transactionId: "TX-10043",
    settlementIds: ["SET-5002"],
    reconciliation: {
      transactionId: "TX-10043",
      reconciled: false,
      exceptions: [
        {
          exceptionId: "EX-2001",
          exceptionType: "AMOUNT_MISMATCH",
          expectedValue: "2500.00",
          observedValue: "2450.00",
          differenceAmount: 50,
          currency: "USD",
          status: "OPEN",
          ...overrides,
        },
      ],
      reconciledAt: "2026-09-30T10:00:00Z",
    },
  };
}

function investigationRow(status: string) {
  return {
    investigation_id: "INV-2001",
    exception_id: "EX-2001",
    transaction_id: "TX-10043",
    exception_type: "AMOUNT_MISMATCH",
    status,
    detected_at: "2026-09-30T10:00:00Z",
    created_at: "2026-09-30T10:00:01Z",
    updated_at: "2026-09-30T10:00:01Z",
  };
}

function renderModal(
  onClose = vi.fn(),
  poll: { pollIntervalMs?: number; pollTimeoutMs?: number } = {},
) {
  return render(
    <MemoryRouter>
      <RunReconciliationModal onClose={onClose} {...poll} />
    </MemoryRouter>,
  );
}

/**
 * Sets the editor's whole value at once.
 *
 * userEvent.type() treats `{` and `[` as key descriptors, so typing raw JSON
 * through it mangles the payload. A change event is what a paste does anyway.
 */
function setEditorValue(value: string) {
  fireEvent.change(screen.getByLabelText(/transaction and settlements/i), {
    target: { value },
  });
}

/** Routes fetch by URL so one test can stub both services at once. */
function stubFetch(handlers: {
  demo?: () => Response | Promise<Response>;
  investigations?: () => Response | Promise<Response>;
}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    void init;
    if (url.includes("/demo/reconcile")) {
      return handlers.demo ? handlers.demo() : jsonResponse(500, {});
    }
    if (url.includes("/investigations")) {
      return handlers.investigations
        ? handlers.investigations()
        : jsonResponse(200, { items: [], total: 0 });
    }
    throw new Error(`unexpected request to ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("the editor and its scenarios", () => {
  it("opens on a scenario and explains what the engine will do", () => {
    renderModal();

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText(/deterministic reconciliation engine using synthetic/i),
    ).toBeInTheDocument();
  });

  it("offers exactly the four V1 scenarios and no duplicate-settlement chip", () => {
    renderModal();

    expect(screen.getByRole("button", { name: "Matching" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Amount Mismatch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Currency Mismatch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Missing Settlement" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /duplicate/i })).not.toBeInTheDocument();
  });

  it("every scenario populates the editor with valid parseable JSON", async () => {
    const user = userEvent.setup();
    renderModal();
    const editor = screen.getByLabelText(/transaction and settlements/i);

    for (const label of [
      "Matching",
      "Amount Mismatch",
      "Currency Mismatch",
      "Missing Settlement",
    ]) {
      await user.click(screen.getByRole("button", { name: label }));
      const parsed = JSON.parse((editor as HTMLTextAreaElement).value);
      expect(parsed.transaction).toMatchObject({
        amount: expect.any(String),
        expectedSettlementAmount: expect.any(String),
        currency: expect.any(String),
      });
      expect(Array.isArray(parsed.settlements)).toBe(true);
    }
  });

  it("the missing-settlement scenario uses an empty list rather than omitting the key", async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole("button", { name: "Missing Settlement" }));

    const editor = screen.getByLabelText(/transaction and settlements/i) as HTMLTextAreaElement;
    expect(JSON.parse(editor.value).settlements).toEqual([]);
  });

  it("the editor is editable and what the user typed is what gets sent", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch({ demo: () => jsonResponse(201, matchResponse()) });
    renderModal();

    setEditorValue(
      '{"transaction":{"amount":"7.00","expectedSettlementAmount":"7.00","currency":"GBP"},"settlements":[]}',
    );
    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      transaction: { amount: "7.00", expectedSettlementAmount: "7.00", currency: "GBP" },
      settlements: [],
    });
  });
});

describe("malformed input", () => {
  it("reports unparseable JSON inline and never calls the backend", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch({ demo: () => jsonResponse(201, matchResponse()) });
    renderModal();

    setEditorValue("{ not json");
    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid JSON/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("clears the parse error once the text changes", async () => {
    const user = userEvent.setup();
    stubFetch({});
    renderModal();

    setEditorValue("nope");
    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    setEditorValue("nope2");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("a matching run", () => {
  it("reports success and says no investigation is required", async () => {
    const user = userEvent.setup();
    stubFetch({ demo: () => jsonResponse(201, matchResponse()) });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByText("Reconciled successfully")).toBeInTheDocument();
    expect(screen.getByText(/No AI investigation required/i)).toBeInTheDocument();
    expect(screen.getByText("TX-10042")).toBeInTheDocument();
  });

  it("does not look for an investigation, because none will exist", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch({ demo: () => jsonResponse(201, matchResponse()) });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));
    await screen.findByText("Reconciled successfully");

    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("exception_id")),
    ).toHaveLength(0);
  });

  it("offers no View Investigation link", async () => {
    const user = userEvent.setup();
    stubFetch({ demo: () => jsonResponse(201, matchResponse()) });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));
    await screen.findByText("Reconciled successfully");

    expect(screen.queryByRole("link", { name: /view investigation/i })).not.toBeInTheDocument();
  });
});

describe("an exception run", () => {
  it("shows the deterministic type and figures exactly as returned", async () => {
    const user = userEvent.setup();
    stubFetch({ demo: () => jsonResponse(201, exceptionResponse()) });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    const panel = await screen.findByText(/Exception detected/i);
    const region = panel.closest(".notice") as HTMLElement;
    expect(within(region).getByText("Amount Mismatch")).toBeInTheDocument();
    expect(within(region).getByText("2,500.00 USD")).toBeInTheDocument();
    expect(within(region).getByText("2,450.00 USD")).toBeInTheDocument();
    expect(within(region).getByText("50.00 USD")).toBeInTheDocument();
    expect(within(region).getByText("EX-2001")).toBeInTheDocument();
    expect(within(region).getByText("TX-10043")).toBeInTheDocument();
  });

  it("omits the difference row when the domain supplies none", async () => {
    // MISSING_SETTLEMENT has no numeric difference. Rendering a zero would be a
    // claim the engine never made.
    const user = userEvent.setup();
    stubFetch({
      demo: () =>
        jsonResponse(
          201,
          exceptionResponse({
            exceptionType: "MISSING_SETTLEMENT",
            expectedValue: "SETTLEMENT_PRESENT",
            observedValue: "NO_SETTLEMENT",
            differenceAmount: null,
          }),
        ),
    });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    await screen.findByText(/Exception detected/i);
    expect(screen.getByText("NO_SETTLEMENT")).toBeInTheDocument();
    expect(screen.queryByText("Difference")).not.toBeInTheDocument();
  });

  it("shows non-monetary values verbatim rather than formatting them as money", async () => {
    const user = userEvent.setup();
    stubFetch({
      demo: () =>
        jsonResponse(
          201,
          exceptionResponse({
            exceptionType: "CURRENCY_MISMATCH",
            expectedValue: "USD",
            observedValue: "EUR",
            differenceAmount: null,
          }),
        ),
    });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    await screen.findByText(/Exception detected/i);
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.getByText("EUR")).toBeInTheDocument();
  });
});

describe("discovering the investigation", () => {
  it("narrates the lifecycle: started, queued, running, then complete", async () => {
    // The investigation now starts on its own, so it appears as PENDING and
    // moves through RUNNING before it has anything to show. Stopping at "a row
    // exists" would link to a page with nothing on it.
    const user = userEvent.setup();
    const statuses = ["PENDING", "RUNNING", "AWAITING_REVIEW"];
    let attempt = 0;
    stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => {
        // Not there on the first look: the event is still in flight.
        if (attempt === 0) {
          attempt += 1;
          return jsonResponse(200, { items: [], total: 0 });
        }
        const status = statuses[Math.min(attempt - 1, statuses.length - 1)];
        attempt += 1;
        return jsonResponse(200, { items: [investigationRow(status)], total: 1 });
      },
    });
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 2000 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    // Only the settled state is asserted here. The pre-row copy is transient —
    // at a 5ms interval the first poll can resolve before an assertion runs —
    // so it is covered by its own test with a stub that never returns a row.
    expect(await screen.findByText("Investigation complete — human review required."))
      .toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view investigation/i })).toHaveAttribute(
      "href",
      "/investigations/INV-2001",
    );
  });

  it("says the investigation started automatically before any row exists", async () => {
    // Deterministic: the row never appears, so the pre-row state is the only
    // one the modal can be in.
    const user = userEvent.setup();
    stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 2000 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(
      await screen.findByText(/Investigation started automatically/i),
    ).toBeInTheDocument();
  });

  it("keeps polling while the investigation is still queued", async () => {
    // PENDING is not a resting place any more: something will move it.
    const user = userEvent.setup();
    const fetchMock = stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => jsonResponse(200, { items: [investigationRow("PENDING")], total: 1 }),
    });
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 120 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));
    expect(await screen.findByText("Investigation queued.")).toBeInTheDocument();

    const early = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(fetchMock.mock.calls.length).toBeGreaterThan(early);
  });

  it("reports a running investigation as using controlled read-only tools", async () => {
    const user = userEvent.setup();
    stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => jsonResponse(200, { items: [investigationRow("RUNNING")], total: 1 }),
    });
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 120 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(
      await screen.findByText(/controlled read-only tools/i),
    ).toBeInTheDocument();
  });

  it.each([
    ["ESCALATED", "Investigation escalated for human review."],
    ["FAILED", "Investigation failed."],
  ])("stops on the terminal status %s and still offers the link", async (status, copy) => {
    const user = userEvent.setup();
    const fetchMock = stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => jsonResponse(200, { items: [investigationRow(status)], total: 1 }),
    });
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 2000 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));
    expect(await screen.findByText(copy)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view investigation/i })).toBeInTheDocument();

    // Settled, so polling stopped rather than running to the deadline.
    const settled = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(fetchMock.mock.calls.length).toBe(settled);
  });

  it("queries by exception id rather than fetching the whole collection", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("exception_id=EX-2001")),
      ).toBe(true),
    );
  });

  it("gives up gracefully instead of polling forever", async () => {
    const user = userEvent.setup();
    const fetchMock = stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      // Never appears — a lost Kafka event, or a stopped consumer.
      investigations: () => jsonResponse(200, { items: [], total: 0 }),
    });
    // Real timings compressed rather than faked: the loop awaits promises
    // between ticks, which a mocked clock deadlocks against.
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 40 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByText(/has not appeared yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Creating investigation/i)).not.toBeInTheDocument();
    // The run is not lost: the exception ID is still on screen — both in the
    // result grid and named in the timeout message, so a visitor can find it.
    expect(screen.getAllByText("EX-2001").length).toBeGreaterThan(0);

    // And it actually stopped: no further lookups after the deadline.
    const after = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(fetchMock.mock.calls.length).toBe(after);
  });

  it("keeps trying when a lookup itself fails", async () => {
    // A failed lookup says nothing about whether the exception was recorded.
    const user = userEvent.setup();
    let attempt = 0;
    stubFetch({
      demo: () => jsonResponse(201, exceptionResponse()),
      investigations: () => {
        attempt += 1;
        if (attempt < 3) return jsonResponse(500, { detail: "boom" });
        return jsonResponse(200, {
          items: [
            {
              investigation_id: "INV-2002",
              exception_id: "EX-2001",
              transaction_id: "TX-10043",
              exception_type: "AMOUNT_MISMATCH",
              status: "PENDING",
              detected_at: "2026-09-30T10:00:00Z",
              created_at: "2026-09-30T10:00:01Z",
              updated_at: "2026-09-30T10:00:01Z",
            },
          ],
          total: 1,
        });
      },
    });
    renderModal(vi.fn(), { pollIntervalMs: 5, pollTimeoutMs: 2000 });

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(
      await screen.findByRole("link", { name: /view investigation/i }),
    ).toHaveAttribute("href", "/investigations/INV-2002");
    expect(attempt).toBeGreaterThanOrEqual(3);
  });
});

describe("backend rejections", () => {
  it("shows a validation message from the server", async () => {
    const user = userEvent.setup();
    stubFetch({
      demo: () =>
        jsonResponse(400, {
          error: "VALIDATION_ERROR",
          message: "transaction.currency must be one of USD, EUR or GBP",
        }),
    });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByText(/rejected that input/i)).toBeInTheDocument();
    expect(screen.getByText(/USD, EUR or GBP/)).toBeInTheDocument();
  });

  it("shows a distinct message when throttled", async () => {
    const user = userEvent.setup();
    stubFetch({
      demo: () =>
        jsonResponse(429, {
          error: "RATE_LIMITED",
          message: "Too many demo reconciliation runs from this client.",
        }),
    });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByText("Too many runs")).toBeInTheDocument();
  });

  it("shows a distinct message when the payload is too large", async () => {
    const user = userEvent.setup();
    stubFetch({
      demo: () =>
        jsonResponse(413, {
          error: "PAYLOAD_TOO_LARGE",
          message: "The request body exceeds the 4096 byte limit for this endpoint.",
        }),
    });
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByText("Payload too large")).toBeInTheDocument();
  });

  it("reports an unreachable backend as unreachable, not as a rejection", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderModal();

    await user.click(screen.getByRole("button", { name: "Run reconciliation" }));

    expect(await screen.findByText(/Could not reach the Financial Core/i)).toBeInTheDocument();
  });
});

describe("closing", () => {
  it("closes on Cancel", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal(onClose);

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderModal(onClose);

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalled();
  });
});
