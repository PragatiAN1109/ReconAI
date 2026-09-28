import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Investigation, InvestigationStatus } from "../types";
import { ReviewPanel } from "./ReviewPanel";

/**
 * The review surface is where a UI mistake becomes a governance mistake. These
 * tests assert the two properties that matter: an action is only offered when
 * the backend would honour it, and nothing on screen changes until the backend
 * has actually accepted the decision.
 */

function investigation(status: InvestigationStatus): Investigation {
  return {
    investigation_id: "INV-1003",
    exception_id: "EX-1009",
    transaction_id: "TX-10010",
    exception_type: "AMOUNT_MISMATCH",
    status,
    detected_at: "2026-09-27T16:42:03Z",
    created_at: "2026-09-27T16:42:04Z",
    updated_at: "2026-09-27T16:44:35Z",
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("review action eligibility", () => {
  it("offers all three actions while awaiting review", () => {
    render(
      <ReviewPanel
        investigation={investigation("AWAITING_REVIEW")}
        recordedDecision={null}
        onReviewed={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Escalate" })).toBeInTheDocument();
  });

  it("offers no approval for an escalated investigation", () => {
    // The real INV-1003 ended ESCALATED. The guardrail's decision is not
    // something a click should appear able to override.
    render(
      <ReviewPanel
        investigation={investigation("ESCALATED")}
        recordedDecision={null}
        onReviewed={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText(/guardrail escalated this investigation/i)).toBeInTheDocument();
  });

  it.each<InvestigationStatus>(["PENDING", "RUNNING", "FAILED", "COMPLETED"])(
    "offers no approval when the status is %s",
    (status) => {
      render(
        <ReviewPanel
          investigation={investigation(status)}
          recordedDecision={null}
          onReviewed={vi.fn()}
        />,
      );

      expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    },
  );

  it("requires a reviewer name before any decision can be made", () => {
    render(
      <ReviewPanel
        investigation={investigation("AWAITING_REVIEW")}
        recordedDecision={null}
        onReviewed={vi.fn()}
      />,
    );

    // Unauthenticated is not the same as anonymous.
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("shows a recorded decision instead of action buttons", () => {
    render(
      <ReviewPanel
        investigation={investigation("COMPLETED")}
        recordedDecision={{
          decision: "APPROVED",
          reviewedBy: "ops.analyst",
          decidedAt: "2026-09-27T17:00:00Z",
          reviewId: "REV-7001",
          resultingStatus: "COMPLETED",
        }}
        onReviewed={vi.fn()}
      />,
    );

    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByText("ops.analyst")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

describe("submitting a decision", () => {
  it("confirms before calling the backend, and calls the real endpoint", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { decision: "APPROVED" }));
    vi.stubGlobal("fetch", fetchMock);
    const onReviewed = vi.fn();

    render(
      <ReviewPanel
        investigation={investigation("AWAITING_REVIEW")}
        recordedDecision={null}
        onReviewed={onReviewed}
      />,
    );

    await user.type(screen.getByLabelText("Reviewer"), "ops.analyst");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    // A dialog appears and nothing has been sent yet.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Record approval" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/investigation/investigations/INV-1003/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ reviewed_by: "ops.analyst", comment: null });

    // State comes from a re-fetch, never from optimism.
    await waitFor(() => expect(onReviewed).toHaveBeenCalled());
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <ReviewPanel
        investigation={investigation("AWAITING_REVIEW")}
        recordedDecision={null}
        onReviewed={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("Reviewer"), "ops.analyst");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("explains a 409 and reloads the true state", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: "Investigation INV-1003 is ESCALATED, not AWAITING_REVIEW.",
        }),
      ),
    );
    const onReviewed = vi.fn();

    render(
      <ReviewPanel
        investigation={investigation("AWAITING_REVIEW")}
        recordedDecision={null}
        onReviewed={onReviewed}
      />,
    );

    await user.type(screen.getByLabelText("Reviewer"), "ops.analyst");
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Record approval" }));

    expect(await screen.findByText("No longer reviewable")).toBeInTheDocument();
    // The conflict triggers a refresh so the screen stops showing a stale state.
    await waitFor(() => expect(onReviewed).toHaveBeenCalled());
  });

  it("surfaces an unreachable backend rather than swallowing it", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(
      <ReviewPanel
        investigation={investigation("AWAITING_REVIEW")}
        recordedDecision={null}
        onReviewed={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("Reviewer"), "ops.analyst");
    await user.click(screen.getByRole("button", { name: "Escalate" }));
    await user.click(screen.getByRole("button", { name: "Record escalation" }));

    expect(await screen.findByText("Service unreachable")).toBeInTheDocument();
  });
});
