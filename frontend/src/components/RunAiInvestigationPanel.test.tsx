import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "../api/client";
import { RunAiInvestigationPanel } from "./RunAiInvestigationPanel";

/**
 * The one action in the console that spends money.
 *
 * What these assert: it never fires on its own, it never renders an AI result
 * itself, and every way the backend can refuse it produces a message that says
 * what actually happened. A visitor told "something went wrong" when the real
 * answer is "the demo is out of AI budget" would reasonably conclude the system
 * is broken.
 */

function apiError(status: number, detail: string) {
  return new ApiError(status, detail, "/api/investigation/investigations/INV-1/run");
}

function renderPanel(
  onRun: () => Promise<never> | Promise<unknown>,
  onCompleted = vi.fn(),
) {
  render(
    <RunAiInvestigationPanel
      investigationId="INV-2001"
      onRun={onRun as () => Promise<never>}
      onCompleted={onCompleted}
    />,
  );
  return { onCompleted };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the action is explicit", () => {
  it("does nothing until the button is pressed", () => {
    const onRun = vi.fn();
    renderPanel(onRun as never);

    expect(onRun).not.toHaveBeenCalled();
  });

  it("says plainly that this is a separate step from reconciliation", () => {
    renderPanel(vi.fn() as never);

    expect(screen.getByText(/not part of reconciliation/i)).toBeInTheDocument();
    expect(screen.getByText(/established deterministically/i)).toBeInTheDocument();
  });

  it("describes the conclusion as advisory and human-gated", () => {
    renderPanel(vi.fn() as never);

    expect(screen.getByText(/advisory/i)).toBeInTheDocument();
    expect(screen.getByText(/requires a human decision/i)).toBeInTheDocument();
  });

  it("runs once per press and disables itself while running", async () => {
    const user = userEvent.setup();
    let resolve: (value: unknown) => void = () => {};
    const onRun = vi.fn(() => new Promise((r) => (resolve = r)));
    renderPanel(onRun as never);

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    const button = screen.getByRole("button", { name: /investigating/i });
    expect(button).toBeDisabled();
    expect(onRun).toHaveBeenCalledTimes(1);

    // Settled inside act so the resulting state update is not reported as an
    // unwrapped one.
    await act(async () => {
      resolve({});
    });
  });
});

describe("after a successful run", () => {
  it("asks the page to reload rather than rendering the result itself", async () => {
    // There must be exactly one place in the console that renders an AI
    // conclusion, and it is not this panel.
    const user = userEvent.setup();
    const { onCompleted } = renderPanel(
      vi.fn().mockResolvedValue({
        investigation_id: "INV-2001",
        status: "ESCALATED",
        guardrail_reason: "Reported confidence 0.7200 is below the review threshold 0.85.",
        recommendation: { classification: "PROCESSOR_FEE" },
        evidence_retrieved: {},
      }) as never,
    );

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/PROCESSOR_FEE/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0.7200/)).not.toBeInTheDocument();
  });
});

describe("how the backend can refuse", () => {
  it("names the AI run limit on a 429 rather than reporting a generic failure", async () => {
    const user = userEvent.setup();
    renderPanel(
      vi
        .fn()
        .mockRejectedValue(
          apiError(429, "You have reached the limit for AI investigation runs in this window."),
        ) as never,
    );

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    expect(await screen.findByText("AI run limit reached")).toBeInTheDocument();
    expect(screen.getByText(/calls a paid model/i)).toBeInTheDocument();
  });

  it("explains a 503 as no model configured, and says detection is unaffected", async () => {
    const user = userEvent.setup();
    renderPanel(
      vi
        .fn()
        .mockRejectedValue(apiError(503, "No investigation model is configured.")) as never,
    );

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    expect(await screen.findByText("No model configured")).toBeInTheDocument();
    expect(screen.getByText(/Deterministic reconciliation is unaffected/i)).toBeInTheDocument();
  });

  it("treats a 409 as already run rather than as an error", async () => {
    const user = userEvent.setup();
    renderPanel(
      vi
        .fn()
        .mockRejectedValue(apiError(409, "Investigation INV-2001 is AWAITING_REVIEW.")) as never,
    );

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    expect(await screen.findByText("Already run")).toBeInTheDocument();
  });

  it("reports a 422 as a genuine outcome with nothing stored", async () => {
    const user = userEvent.setup();
    renderPanel(
      vi
        .fn()
        .mockRejectedValue(
          apiError(422, "cited evidence that was never retrieved: FEE_RULE/FR-999"),
        ) as never,
    );

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    expect(
      await screen.findByText(/produced no usable result/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/discarded rather than shown/i)).toBeInTheDocument();
  });

  it("reports an unreachable service as unreachable", async () => {
    const user = userEvent.setup();
    renderPanel(vi.fn().mockRejectedValue(new NetworkError("/run")) as never);

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));

    expect(
      await screen.findByText(/Could not reach the Investigation Service/i),
    ).toBeInTheDocument();
  });

  it("re-enables the button after a failure so the run can be retried", async () => {
    const user = userEvent.setup();
    renderPanel(vi.fn().mockRejectedValue(apiError(429, "slow down")) as never);

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));
    await screen.findByText("AI run limit reached");

    expect(screen.getByRole("button", { name: /run ai investigation/i })).toBeEnabled();
  });

  it("does not report completion when the run failed", async () => {
    const user = userEvent.setup();
    const { onCompleted } = renderPanel(
      vi.fn().mockRejectedValue(apiError(503, "no model")) as never,
    );

    await user.click(screen.getByRole("button", { name: /run ai investigation/i }));
    await screen.findByText("No model configured");

    expect(onCompleted).not.toHaveBeenCalled();
  });
});
