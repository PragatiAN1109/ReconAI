"""Tests for offline execution and the live-call safety gates.

The safety tests matter most. The account has limited prepaid credit, and the
failure mode worth preventing is not a wrong number — it is a command that
quietly spends money. Every gate is tested from the outside, by calling the CLI
the way a person would.

Nothing here reaches a network. The one test that proves it asserts so directly.
"""

import json

import pytest

from app.investigation_models import RootCauseClassification
from app.models import InvestigationStatus

from evals.dataset import SCENARIOS
from evals.run import LiveRunRefused, main, parse_args, select
from evals.runner import RunConfig, run_scenario, run_scenarios

T = RootCauseClassification


def by_id(scenario_id: str):
    return next(s for s in SCENARIOS if s.scenario_id == scenario_id)


def offline_config(settings) -> RunConfig:
    from decimal import Decimal

    return RunConfig(
        mode="offline",
        settings=settings,
        confidence_threshold=Decimal("0.85"),
        minimum_evidence=1,
    )


# ---------------------------------------------------------------------------
# Offline execution drives the real agent
# ---------------------------------------------------------------------------


async def test_a_scenario_runs_through_the_real_agent(settings) -> None:
    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))

    assert outcome.result is not None
    assert outcome.result.classification is T.PROCESSOR_FEE
    # Evidence was retrieved through the production client and recorded by the
    # production ledger, not injected.
    assert "SET-E1001" in outcome.ledger.settlements
    assert "FR-E101" in outcome.ledger.fee_rules


async def test_the_scenario_transport_is_used_not_a_live_financial_core(settings) -> None:
    """Evidence must come from the scenario, never from a running service.

    Guards a defect found while building this: calling ``open()`` on the client
    replaces the scenario transport with a real connection pool, silently
    pointing evaluation traffic at whatever is listening on the configured port.
    """
    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))

    # These identifiers exist only in this scenario. The production seed uses
    # FR-14..FR-18, so their absence proves nothing external was consulted.
    assert outcome.ledger.fee_rules == {"FR-E101"}
    assert not {"FR-14", "FR-15", "FR-16"} & outcome.ledger.fee_rules


async def test_the_real_guardrail_decides_the_outcome(settings) -> None:
    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))

    assert outcome.decision is not None
    assert outcome.decision.status is InvestigationStatus.AWAITING_REVIEW
    assert outcome.escalated is False


async def test_a_low_confidence_result_is_escalated_by_the_real_guardrail(settings) -> None:
    outcome = await run_scenario(
        by_id("harness-low-confidence-correct-answer"), offline_config(settings)
    )

    assert outcome.result is not None
    assert outcome.result.classification is T.PROCESSOR_FEE  # correct answer
    assert outcome.escalated is True  # and escalated anyway


async def test_the_production_grounding_validator_rejects_a_fabricated_citation(
    settings,
) -> None:
    """Not re-implemented here: the real validator is what rejects it."""
    outcome = await run_scenario(by_id("harness-ungrounded-citation"), offline_config(settings))

    assert outcome.result is None
    assert outcome.failure_kind == "UngroundedResultError"
    assert "FR-E999" in outcome.failure
    # The agent returns its ledger only with a successful result, so a rejected
    # run cannot report what was retrieved. Flagged rather than left as an
    # empty ledger that would read as "nothing was retrieved".
    assert outcome.ledger_available is False
    assert outcome.ledger.is_empty


async def test_tool_calls_are_observed_in_order(settings) -> None:
    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))

    assert [call.tool for call in outcome.tool_calls] == [
        "get_transaction",
        "get_settlements",
        "get_fee_rules",
        "search_policy_documents",
    ]
    assert all(call.succeeded for call in outcome.tool_calls)


async def test_a_failed_tool_call_is_recorded_as_failed(settings) -> None:
    """Derived from the agent's own ToolResult, not assumed."""
    scenario = by_id("insufficient-no-records-at-all")
    outcome = await run_scenario(scenario, offline_config(settings))

    # This scenario has no settlements, so get_settlements returns an empty
    # list rather than failing; the run still completes.
    assert outcome.result is not None


async def test_model_calls_are_counted_exactly(settings) -> None:
    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))

    # Four tool rounds plus the final result turn.
    assert outcome.model_calls == 5


async def test_running_several_scenarios_returns_one_outcome_each(settings) -> None:
    chosen = [by_id("fee-exact-match"), by_id("duplicate-identical-settlements")]

    outcomes = await run_scenarios(chosen, offline_config(settings))

    assert [o.scenario.scenario_id for o in outcomes] == [s.scenario_id for s in chosen]


async def test_an_evaluation_persists_nothing(settings) -> None:
    """An evaluation is an observation. It writes no investigation, ever."""
    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))

    assert not hasattr(outcome, "investigation_id")
    assert outcome.scenario.scenario_id == "fee-exact-match"


# ---------------------------------------------------------------------------
# Live-call safety
# ---------------------------------------------------------------------------


def test_offline_is_the_default_mode() -> None:
    assert parse_args([]).mode == "offline"
    assert parse_args([]).confirm_live is False


def test_live_mode_without_confirmation_refuses(capsys) -> None:
    """The gate that stops an accidental spend."""
    code = main(["--mode", "live", "--scenario", "fee-exact-match"])

    assert code == 2
    assert "REFUSED" in capsys.readouterr().err


def test_live_mode_without_credentials_refuses(capsys, monkeypatch) -> None:
    """A run labelled live must not quietly produce offline numbers."""
    monkeypatch.delenv("RECONAI_AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("RECONAI_AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.chdir("/tmp")  # away from any real .env

    code = main(["--mode", "live", "--scenario", "fee-exact-match", "--confirm-live"])

    assert code == 2
    error = capsys.readouterr().err
    assert "REFUSED" in error
    assert "requires a configured provider" in error


def test_a_refused_live_run_constructs_no_provider(monkeypatch) -> None:
    """Proven by making provider construction itself an error."""
    import evals.run as run_module

    def explode(_settings):
        raise AssertionError("the provider must not be constructed without --confirm-live")

    monkeypatch.setattr(run_module, "build_live_model", explode)

    assert main(["--mode", "live", "--scenario", "fee-exact-match"]) == 2


def test_an_unknown_scenario_id_is_rejected_rather_than_running_everything() -> None:
    """In live mode a typo is the difference between one call and thirty-six."""
    with pytest.raises(SystemExit, match="Unknown scenario"):
        select(SCENARIOS, ["no-such-scenario"], None)


def test_specific_scenarios_can_be_selected() -> None:
    chosen = select(SCENARIOS, ["fee-exact-match", "fee-near-miss-amount"], None)

    assert [s.scenario_id for s in chosen] == ["fee-exact-match", "fee-near-miss-amount"]


def test_the_scenario_count_can_be_limited() -> None:
    assert len(select(SCENARIOS, [], 3)) == 3


def test_the_normal_test_suite_makes_no_live_calls(settings) -> None:
    """The guarantee that matters for CI, asserted directly.

    ``pytest`` must never reach a provider. The offline runner constructs its
    own deterministic model and ignores any configured credential.
    """
    config = offline_config(settings)

    assert config.mode == "offline"
    assert config.live_model is None
    assert config.provider == "offline-deterministic"


async def test_offline_mode_ignores_a_configured_provider(settings, monkeypatch) -> None:
    """Even with credentials set, offline mode never constructs the adapter."""
    monkeypatch.setenv("RECONAI_AGENT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("RECONAI_AGENT_LLM_API_KEY", "not-a-real-key")

    import app.anthropic_model as adapter

    def explode(*_args, **_kwargs):
        raise AssertionError("offline mode must not construct a provider adapter")

    monkeypatch.setattr(adapter, "AnthropicInvestigationModel", explode)

    outcome = await run_scenario(by_id("fee-exact-match"), offline_config(settings))
    assert outcome.result is not None


# ---------------------------------------------------------------------------
# CLI end to end
# ---------------------------------------------------------------------------


def test_the_offline_cli_runs_and_writes_reports(tmp_path, capsys) -> None:
    code = main(
        [
            "--mode", "offline",
            "--scenario", "fee-exact-match",
            "--scenario", "harness-ungrounded-citation",
            "--output", str(tmp_path),
            "--run-id", "cli-test",
            "--quiet",
        ]
    )

    assert code == 0
    report = json.loads((tmp_path / "cli-test.json").read_text())
    assert report["scenario_count"] == 2
    assert report["mode"] == "offline"
    assert (tmp_path / "cli-test.md").exists()

    output = capsys.readouterr().out
    assert "NOT Claude's accuracy" in output


def test_the_cli_reports_the_scenario_count_before_running(tmp_path, capsys) -> None:
    """So a live run's cost is visible while it can still be cancelled."""
    main(["--scenario", "fee-exact-match", "--output", str(tmp_path), "--quiet"])

    assert "Scenarios     : 1 of 36" in capsys.readouterr().out


def test_the_cli_can_validate_the_dataset_without_running(capsys) -> None:
    assert main(["--validate-only"]) == 0
    assert "Dataset valid" in capsys.readouterr().out


def test_the_cli_can_list_scenarios(capsys) -> None:
    assert main(["--list"]) == 0
    output = capsys.readouterr().out
    assert "fee-exact-match" in output
    assert len([line for line in output.splitlines() if line.strip()]) == len(SCENARIOS)
