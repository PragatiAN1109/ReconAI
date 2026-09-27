"""Rendering a run as JSON and as Markdown.

Both carry the same data. The JSON is for diffing runs and for whatever comes
next; the Markdown is for a person deciding whether the system got better or
worse.

Every report opens with what the numbers are *of*. An offline run's
"classification accuracy" is a property of the scripted provider, and a reader
who skims will take it for a claim about Claude unless the document refuses to
let them. That banner is not decoration.
"""

import json
import platform
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from evals.metrics import Summary
from evals.runner import RunConfig, ScenarioOutcome

OFFLINE_BANNER = (
    "> **These numbers measure the evaluation harness, not a language model.**\n"
    "> Offline mode replays a deterministic script written alongside each scenario. "
    "Its classification accuracy reflects what those scripts say and is **not evidence "
    "of Claude's accuracy**. What offline mode does prove: scenarios are well formed, "
    "evidence is retrievable, the production grounding validator rejects fabricated "
    "citations, the deterministic guardrail routes as documented, and every metric "
    "computes correctly — including on deliberately wrong behaviour."
)

LIVE_BANNER = (
    "> **Live run against a real provider.** Classification, grounding and tool-use "
    "figures below describe the configured model's behaviour on synthetic scenarios. "
    "They are a controlled measurement, not a claim about production performance on "
    "real financial data — see Limitations in `evals/README.md`."
)


def _scenario_rows(outcomes: Sequence[ScenarioOutcome]) -> list[dict]:
    rows = []
    for outcome in outcomes:
        scenario = outcome.scenario
        expected_ids = scenario.expected_identifiers()
        cited = [reference.reference for reference in (outcome.result.evidence if outcome.result else [])]
        rows.append(
            {
                "scenario_id": scenario.scenario_id,
                "title": scenario.title,
                "difficulty": scenario.difficulty.value,
                "exception_type": scenario.exception_type.value,
                "expected_classification": scenario.expected_classification.value,
                "actual_classification": outcome.classification,
                "acceptable_classifications": sorted(
                    c.value for c in scenario.acceptable_classifications
                ),
                "strict_match": bool(
                    outcome.result
                    and outcome.result.classification == scenario.expected_classification
                ),
                "acceptable_match": bool(
                    outcome.result and scenario.is_acceptable(outcome.result.classification)
                ),
                "confidence": (
                    str(outcome.result.confidence_value) if outcome.result else None
                ),
                "expected_escalate": scenario.should_escalate,
                "actual_guardrail": (
                    outcome.decision.status.value if outcome.decision else None
                ),
                "guardrail_reason": outcome.decision.reason if outcome.decision else None,
                "tools_called": [call.tool for call in outcome.tool_calls],
                "required_tools": sorted(scenario.required_tools),
                "forbidden_tools_called": sorted(
                    {c.tool for c in outcome.tool_calls} & scenario.forbidden_tools
                ),
                "failed_tool_calls": [c.tool for c in outcome.tool_calls if not c.succeeded],
                "evidence_cited": cited,
                "expected_evidence": expected_ids,
                "evidence_covered": [i for i in expected_ids if i in cited],
                "evidence_retrieved_available": outcome.ledger_available,
                "evidence_retrieved": {
                    "transactions": sorted(outcome.ledger.transactions),
                    "settlements": sorted(outcome.ledger.settlements),
                    "fee_rules": sorted(outcome.ledger.fee_rules),
                    "policy_documents": sorted(outcome.ledger.policy_documents),
                },
                "grounded": outcome.grounded,
                "model_calls": outcome.model_calls,
                "latency_seconds": round(outcome.latency_seconds, 4),
                "failure": outcome.failure,
                "failure_kind": outcome.failure_kind,
            }
        )
    return rows


def build_report(
    outcomes: Sequence[ScenarioOutcome], summary: Summary, config: RunConfig, run_id: str
) -> dict:
    return {
        "run_id": run_id,
        "mode": config.mode,
        "measures": (
            "the evaluation harness and scripted provider"
            if config.mode != "live"
            else "the configured live model"
        ),
        "timestamp": datetime.now(UTC).isoformat(),
        "provider": config.provider,
        "model": config.model_name,
        "scenario_count": len(outcomes),
        "guardrail_policy": {
            "confidence_threshold": str(config.confidence_threshold),
            "minimum_evidence": config.minimum_evidence,
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "metrics": summary.as_dict(),
        "scenarios": _scenario_rows(outcomes),
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(report: dict) -> str:
    metrics = report["metrics"]
    live = report["mode"] == "live"
    lines: list[str] = []
    add = lines.append

    add(f"# ReconAI Evaluation — `{report['run_id']}`")
    add("")
    add(LIVE_BANNER if live else OFFLINE_BANNER)
    add("")
    add(f"| | |\n|---|---|")
    add(f"| Mode | **{report['mode'].upper()}** |")
    add(f"| Measures | {report['measures']} |")
    add(f"| Provider | `{report['provider']}` |")
    add(f"| Model | `{report['model']}` |")
    add(f"| Scenarios | {report['scenario_count']} |")
    add(f"| Timestamp | {report['timestamp']} |")
    add(
        f"| Guardrail | threshold {report['guardrail_policy']['confidence_threshold']}, "
        f"min evidence {report['guardrail_policy']['minimum_evidence']} |"
    )
    add("")

    classification = metrics["classification"]
    add("## Classification")
    add("")
    add("Strict counts exact matches only. Acceptable additionally counts alternatives a")
    add("scenario explicitly declared defensible; the two are never merged.")
    add("")
    add("| Metric | Value |")
    add("|---|---|")
    add(f"| Strict accuracy | **{_pct(classification['strict_accuracy'])}** "
        f"({classification['strict_correct']}/{classification['total']}) |")
    add(f"| Acceptable accuracy | {_pct(classification['acceptable_accuracy'])} "
        f"({classification['acceptable_correct']}/{classification['total']}) |")
    add(f"| Produced no result | {classification['no_result']} |")
    add("")

    grounding = metrics["grounding"]
    add("## Evidence grounding")
    add("")
    add("Whether citations were backed by evidence actually retrieved. The production")
    add("validator is authoritative: a result containing one unbacked citation is")
    add("rejected whole, so rejections — not partial citation rates — carry the signal.")
    add("")
    add("| Metric | Value |")
    add("|---|---|")
    add(f"| Results produced | {grounding['results_produced']} |")
    add(f"| **Ungrounded results (rejected)** | **{grounding['ungrounded_results']}** |")
    add(f"| Citations | {grounding['grounded_citations']}/{grounding['total_citations']} grounded |")
    add(f"| Citation grounding rate | {_pct(grounding['citation_grounding_rate'])} |")
    add("")

    coverage = metrics["evidence_coverage"]
    add("## Evidence coverage")
    add("")
    add("Distinct from grounding: a result can cite only real records and still miss the")
    add("one that explains the case. Only scenarios naming specific identifiers are scored.")
    add("")
    add(f"- Coverage: **{_pct(coverage['coverage'])}** "
        f"({coverage['cited_total']}/{coverage['expected_total']} expected identifiers cited)")
    add(f"- Scenarios scored: {coverage['scenarios_scored']}")
    add("")

    tools = metrics["tools"]
    add("## Tool selection")
    add("")
    add("Optional tools are never penalised — gathering evidence that turns out not to")
    add("matter is normal investigative behaviour.")
    add("")
    add("| Metric | Value |")
    add("|---|---|")
    add(f"| Required tool recall | **{_pct(tools['required_tool_recall'])}** "
        f"({tools['required_called']}/{tools['required_expected']}) |")
    add(f"| Forbidden tool calls | {tools['forbidden_call_count']} "
        f"(in {tools['scenarios_with_forbidden_calls']} scenarios) |")
    add(f"| Failed tool calls | {tools['failed_tool_calls']}/{tools['total_tool_calls']} |")
    add("")
    if tools["calls_by_tool"]:
        add("| Tool | Calls |")
        add("|---|---|")
        for tool, count in tools["calls_by_tool"].items():
            add(f"| `{tool}` | {count} |")
        add("")

    escalation = metrics["escalation"]
    add("## Escalation")
    add("")
    add("Computed from the **real deterministic guardrail**, never from the classification.")
    add("A correct escalation is a successful safety outcome, not a failure.")
    add("")
    add(f"- Escalation accuracy: **{_pct(escalation['accuracy'])}** "
        f"({escalation['scored']} scored, {escalation['no_decision']} without a decision)")
    add("")
    add("| | Actual: ESCALATED | Actual: AWAITING_REVIEW |")
    add("|---|---|---|")
    add(f"| **Expected: escalate** | {escalation['expected_escalate_actual_escalate']} ✓ "
        f"| {escalation['expected_escalate_actual_review']} ⚠️ |")
    add(f"| **Expected: review** | {escalation['expected_review_actual_escalate']} "
        f"| {escalation['expected_review_actual_review']} ✓ |")
    add("")
    unsafe = escalation["unsafe_review_count"]
    if unsafe:
        add(f"⚠️ **{unsafe} case(s) needing a human were routed as confident findings.** "
            "This is the cell that matters most; weight it above raw accuracy.")
    else:
        add("No case needing a human was routed as a confident finding.")
    add("")

    latency = metrics["latency"]
    add(f"## Latency — {latency['label']}")
    add("")
    add("Offline and live latency are not comparable and are labelled to prevent it.")
    add("")
    add("| Metric | Seconds |")
    add("|---|---|")
    add(f"| Mean | {latency['mean_seconds']:.4f} |")
    add(f"| Median | {latency['median_seconds']:.4f} |")
    add(f"| p95 | {latency['p95_seconds']:.4f} |")
    add(f"| Max | {latency['max_seconds']:.4f} |")
    add("")

    tokens, cost = metrics["tokens"], metrics["cost"]
    add("## Tokens and cost")
    add("")
    if tokens["observable"]:
        add(f"- Input tokens: {tokens['input_tokens']}")
        add(f"- Output tokens: {tokens['output_tokens']}")
    else:
        add(f"- Token usage: **NOT OBSERVABLE** — {tokens['note']}")
    add(f"- Provider calls: {tokens['provider_calls']}")
    if cost["calculated"]:
        add(f"- Estimated cost: **${cost['estimated_usd']:.6f}** — {cost['note']}")
    else:
        add(f"- Cost: **NOT CALCULATED** — {cost['note']}")
    add("")

    add("## Per-scenario results")
    add("")
    add("| Scenario | Difficulty | Expected | Actual | Strict | Esc. exp/act | Grounded | Tools | Latency |")
    add("|---|---|---|---|---|---|---|---|---|")
    for row in report["scenarios"]:
        strict = "✓" if row["strict_match"] else ("~" if row["acceptable_match"] else "✗")
        grounded = "✓" if row["grounded"] else "✗"
        expected_esc = "esc" if row["expected_escalate"] else "rev"
        actual = row["actual_guardrail"] or "—"
        add(
            f"| `{row['scenario_id']}` | {row['difficulty']} | "
            f"{row['expected_classification']} | {row['actual_classification'] or '—'} | "
            f"{strict} | {expected_esc}/{actual} | {grounded} | "
            f"{len(row['tools_called'])} | {row['latency_seconds']:.3f}s |"
        )
    add("")

    failures = [r for r in report["scenarios"] if r["failure"]]
    if failures:
        add("### Runs that produced no result")
        add("")
        for row in failures:
            add(f"- `{row['scenario_id']}` — **{row['failure_kind']}**: {row['failure']}")
        add("")

    add("---")
    add("")
    add("Deliberately no single composite score: classification, grounding, coverage,")
    add("tool use and escalation measure different properties that trade off against")
    add("each other, and averaging them would hide exactly those trade-offs.")
    return "\n".join(lines)


def write_reports(report: dict, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    run_id = report["run_id"]
    json_path = directory / f"{run_id}.json"
    markdown_path = directory / f"{run_id}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
