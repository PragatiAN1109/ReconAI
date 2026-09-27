"""Command-line entry point for evaluation runs.

    python -m evals.run --mode offline
    python -m evals.run --mode live --scenario fee-exact-match --confirm-live

Offline is the default and the only mode that can run by accident. Reaching a
paid provider requires three independent things to line up — ``--mode live``,
``--confirm-live``, and configured credentials — and the absence of any one of
them is an error rather than a quiet fallback.

There are no retries. A retry loop is a cost multiplier that hides behind a
transient failure, and a failed live scenario is cheaper to rerun deliberately
than to retry automatically.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.config import Settings

from evals.dataset import SCENARIOS
from evals.metrics import summarise
from evals.report import build_report, write_reports
from evals.runner import RunConfig, run_scenarios
from evals.scenario import Scenario, validate_dataset

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "eval-results"


class LiveRunRefused(RuntimeError):
    """A live run was requested but the safety conditions were not met.

    Its own type so the refusal is testable, and so nothing can mistake it for
    a provider failure and "recover" by trying again.
    """


def policy_document_ids(settings: Settings) -> set[str]:
    """Document identifiers actually present in the corpus on disk."""
    from app.policy_search import PolicySearch  # noqa: PLC0415

    search = PolicySearch(settings.policy_corpus_path)
    return {section.document_id for section in search._sections}  # noqa: SLF001


def select(scenarios: Sequence[Scenario], ids: Sequence[str], limit: int | None) -> list[Scenario]:
    """Pick the scenarios to run, failing loudly on an unknown identifier.

    A typo must not silently run the whole suite — in live mode that is the
    difference between one paid call and thirty-six.
    """
    chosen = list(scenarios)
    if ids:
        known = {scenario.scenario_id: scenario for scenario in scenarios}
        unknown = [identifier for identifier in ids if identifier not in known]
        if unknown:
            raise SystemExit(
                f"Unknown scenario id(s): {unknown}\n"
                f"Available: {', '.join(sorted(known))}"
            )
        chosen = [known[identifier] for identifier in ids]
    if limit is not None:
        chosen = chosen[:limit]
    return chosen


def build_live_model(settings: Settings):
    """Construct the real provider, refusing clearly if it is not configured."""
    if not settings.investigation_model_configured:
        raise LiveRunRefused(
            "Live mode requires a configured provider. Set RECONAI_AGENT_LLM_PROVIDER "
            "and RECONAI_AGENT_LLM_API_KEY (agent-service/.env is read automatically). "
            "Refusing rather than falling back to the offline provider, because a run "
            "labelled 'live' must not quietly produce offline numbers."
        )

    from app.anthropic_model import AnthropicInvestigationModel  # noqa: PLC0415

    assert settings.llm_api_key is not None
    return AnthropicInvestigationModel(
        api_key=settings.llm_api_key.get_secret_value(), model=settings.llm_model
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Run ReconAI investigation evaluations.",
    )
    parser.add_argument(
        "--mode",
        choices=("offline", "live"),
        default="offline",
        help="offline (default, free, no network) or live (real provider, costs money)",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        dest="scenarios",
        metavar="ID",
        help="run only this scenario; repeatable",
    )
    parser.add_argument("--limit", type=int, default=None, help="run at most N scenarios")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required for --mode live; without it no provider call is made",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="report directory")
    parser.add_argument("--run-id", default=None, help="report filename stem")
    parser.add_argument("--input-cost-per-million", type=float, default=None)
    parser.add_argument("--output-cost-per-million", type=float, default=None)
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--validate-only", action="store_true", help="validate dataset and exit")
    parser.add_argument("--quiet", action="store_true", help="suppress progress logging")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()

    # The dataset is validated before anything else, in every mode. A bad
    # benchmark produces numbers that look like measurement and are not.
    validate_dataset(SCENARIOS, policy_document_ids(settings))

    if args.validate_only:
        print(f"Dataset valid: {len(SCENARIOS)} scenarios.")
        return 0

    if args.list:
        for scenario in SCENARIOS:
            print(
                f"{scenario.scenario_id:45} {scenario.expected_classification.value:22} "
                f"{scenario.difficulty.value:12} escalate={scenario.should_escalate}"
            )
        return 0

    selected = select(SCENARIOS, args.scenarios, args.limit)

    # Announced before anything runs, so the cost of a live run is visible
    # while it can still be cancelled.
    print(f"Mode          : {args.mode.upper()}")
    print(f"Scenarios     : {len(selected)} of {len(SCENARIOS)}")
    if selected and len(selected) <= 10:
        for scenario in selected:
            print(f"                - {scenario.scenario_id}")

    live_model = None
    if args.mode == "live":
        if not args.confirm_live:
            raise LiveRunRefused(
                f"Live mode would make real provider calls for {len(selected)} scenario(s) "
                "and spend real money. Re-run with --confirm-live to proceed. "
                "Nothing was called."
            )
        live_model = build_live_model(settings)
        print(f"Provider      : {settings.llm_provider} / {settings.llm_model}")
        print("⚠️  Making real, billable provider calls. No retries are performed.")
    else:
        print("Provider      : offline deterministic (no network, no credentials, no cost)")

    config = RunConfig(
        mode=args.mode,
        settings=settings,
        live_model=live_model,
        max_tool_rounds=settings.investigation_max_tool_rounds,
        confidence_threshold=settings.review_confidence_threshold,
        minimum_evidence=settings.review_minimum_evidence,
    )

    outcomes = await run_scenarios(selected, config)
    summary = summarise(
        outcomes,
        args.mode,
        input_cost_per_million=args.input_cost_per_million,
        output_cost_per_million=args.output_cost_per_million,
    )

    run_id = args.run_id or (
        f"{args.mode}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    report = build_report(outcomes, summary, config, run_id)
    json_path, markdown_path = write_reports(report, args.output)

    print()
    if args.mode != "live":
        print("NOTE: offline metrics measure the harness and scripted provider,")
        print("      NOT Claude's accuracy.")
    print(f"Strict classification accuracy : {summary.classification.strict_accuracy:.1%}")
    print(f"Acceptable accuracy            : {summary.classification.acceptable_accuracy:.1%}")
    print(f"Ungrounded (rejected) results  : {summary.grounding.ungrounded_results}")
    print(f"Required tool recall           : {summary.tools.required_tool_recall:.1%}")
    print(f"Escalation accuracy            : {summary.escalation.accuracy:.1%}")
    print(f"Unsafe (needed human, routed as finding): {summary.escalation.unsafe_review_count}")
    print(f"{summary.latency.label} p95      : {summary.latency.p95_seconds:.4f}s")
    print()
    print(f"JSON     : {json_path}")
    print(f"Markdown : {markdown_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # The agent logs every tool call at INFO, which is noise here; the runner's
    # own progress lines are the useful signal.
    logging.getLogger("app").setLevel(logging.WARNING)
    try:
        return asyncio.run(_run(args))
    except LiveRunRefused as error:
        print(f"\nREFUSED: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"\nDATASET INVALID: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
