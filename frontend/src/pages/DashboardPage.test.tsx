import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "./DashboardPage";

/**
 * The playground's entry point.
 *
 * Only the wiring is under test here: the queue itself is covered by the utils
 * it delegates to, and the modal has its own suite. What matters is that the
 * action is reachable from the queue header, that it opens nothing until it is
 * pressed, and that closing it refreshes the list — a run may have created an
 * investigation, and a stale queue would look like the run had no effect.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the Run Reconciliation action", () => {
  it("appears in the queue header rather than as a navigation section", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { items: [], total: 0 })),
    );
    renderDashboard();

    const button = await screen.findByRole("button", { name: /run reconciliation/i });
    expect(button.closest(".page-head")).not.toBeNull();
  });

  it("is offered even when the investigation queue fails to load", async () => {
    // The playground exercises the Financial Core. An Investigation Service
    // outage should not remove the one interactive thing on the page.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderDashboard();

    expect(
      await screen.findByRole("button", { name: /run reconciliation/i }),
    ).toBeInTheDocument();
  });

  it("opens nothing until it is pressed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { items: [], total: 0 })),
    );
    renderDashboard();

    await screen.findByRole("button", { name: /run reconciliation/i });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the playground modal when pressed", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { items: [], total: 0 })),
    );
    renderDashboard();

    await user.click(await screen.findByRole("button", { name: /run reconciliation/i }));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(
      screen.getByText(/deterministic reconciliation engine using synthetic/i),
    ).toBeInTheDocument();
  });

  it("reloads the queue when the modal closes, so a new investigation shows up", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(200, { items: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    renderDashboard();

    await user.click(await screen.findByRole("button", { name: /run reconciliation/i }));
    const before = fetchMock.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
