# ReconAI Evaluations

Measurement for the existing investigation system. This framework builds no new
reasoning: every judgement is made by production code, and the harness supplies
only the scenario evidence, the provider, and the scoring.

> **This phase measures. It does not optimise.** Nothing here tunes the prompt,
> adjusts the confidence threshold, or changes the agent. A measurement system
> that is modified until it reports good numbers has stopped being one.

## Why ReconAI has evals

The vertical test proved the pipeline works on one case. One case cannot tell
you whether the investigator reasons or pattern-matches, whether it fabricates
citations under pressure, or whether it escalates the cases that need a human.
Those are properties of a *distribution* of cases, and you cannot improve what
you have not measured — nor can you safely claim quality you have not measured.

The specific risks this framework exists to detect:

- **Fabricated evidence.** An investigator citing a fee rule that does not
  exist is worse than one that says "I don't know".
- **Pattern matching mistaken for reasoning.** A 50.00 difference next to a
  50.00 fee rule is *not* an explanation if the rule is withdrawn, belongs to
  another processor, or is denominated in another currency.
- **Unsafe confidence.** A case that needed a human routed as a confident
  finding is the failure that costs money.
- **Silent regression.** A prompt or model change that degrades any of the
  above.

## Offline vs live

|  | Offline | Live |
|---|---|---|
| Provider | Deterministic script | Real Anthropic |
| Network | None | Yes |
| Credentials | Not required | Required |
| Cost | Free | **Real money** |
| Safe in CI | Yes | Never runs automatically |
| Measures | **The harness** | **The model** |

### Offline metrics are NOT evidence of Claude's quality

This is the single most important thing to understand about this framework.

Offline mode replays a script written alongside each scenario. If that script
says `PROCESSOR_FEE`, then "classification accuracy" is a fact about the script
and nothing else. Every generated report says so at the top.

What offline mode **does** prove, and proves well:

- scenarios are well formed and internally consistent;
- evidence is retrievable through the real `FinancialCoreClient`;
- the **production grounding validator** rejects fabricated citations;
- the **production guardrail** routes results exactly as documented;
- every metric computes correctly — including on deliberately wrong behaviour.

Five scenarios are named `harness-*` and exist purely for that last point. They
script a wrong classification, a fabricated citation, a skipped required tool, a
forbidden tool call, and a correct answer held at low confidence. Their presence
is why offline strict accuracy is **94.4% rather than 100%**: a dataset whose
script always agrees with the answer key would measure nothing.

## Metrics

No composite score. Classification, grounding, coverage, tool use and escalation
measure different properties that trade off against one another — a system that
escalates everything scores perfectly on safety and uselessly on accuracy, and
one number cannot express that.

### Classification accuracy
`strict` counts exact matches. `acceptable` additionally counts alternatives a
scenario explicitly declared defensible. Reported separately and never merged:
folding alternatives into "accuracy" quietly inflates the headline.

### Evidence grounding
Whether every citation was backed by evidence actually retrieved. The production
validator is authoritative and rejects a result containing *one* unbacked
citation — rejection is all-or-nothing by design, so the signal lives in
`ungrounded_results`, not in a partial citation rate. On a live run that number
is the fabrication rate, and it is the most important figure this framework
produces.

The metric re-checks each citation against the ledger independently, rather than
trusting that a surviving result must be grounded. It exists to catch the
validator being wrong, not to restate its output.

### Evidence coverage
Distinct from grounding, and the distinction matters: a result can cite only
real records and still miss the one that explains the case. Grounding asks *is
this true?*; coverage asks *is this enough?* Only scenarios naming specific
identifiers are scored; categorical requirements ("some policy document") are
excluded rather than guessed at.

### Tool selection
`required_tool_recall` and `forbidden_tool_call_count`. Optional tools are never
penalised — gathering evidence that turns out not to matter is normal
investigative behaviour, and a metric discouraging it would train the wrong habit.

### Unsupported citations
Measured from **observable structured citations only**. We do not attempt to
judge arbitrary prose claims in `rootCause`, and we do not inspect hidden
reasoning. A model could in principle assert something unsupported in prose
while citing only real identifiers; this framework would not detect it, and
claiming otherwise would be inventing a metric that cannot be verified.

### Escalation accuracy
Computed from the **real deterministic guardrail**, never from the
classification. Those are different questions: a correct classification held at
low confidence is still correctly escalated.

Reported as a full confusion matrix. The cell that matters most is
*expected-escalate / actual-review* — a case that needed a human routed as a
confident finding. Weight it above raw accuracy.

> **A correct escalation is a successful safety outcome, not a failure.**
> Escalation is the system declining to present a weak explanation as a finding.
> A high classification rate achieved by never escalating would be worse than a
> lower one that escalates honestly. Do not read "escalation" as "error".

### Latency
Mean, median, p95 (nearest-rank, so the value always actually occurred), and max.
Labelled `OFFLINE HARNESS LATENCY` or `LIVE MODEL LATENCY` — they differ by
orders of magnitude and are not comparable.

### Tokens — currently NOT OBSERVABLE
The Anthropic adapter does not surface `response.usage` through the provider
abstraction, so live runs report `NOT OBSERVABLE` rather than zero. Zero would be
a fabricated measurement.

**Proposed** (not implemented — this phase does not change production code): add
an optional `usage` field to `AssistantTurn`, populated by the adapter and
ignored by every existing caller. That is backward compatible and does not alter
the provider interface. It needs your approval before anyone writes it.

### Cost
Only calculated when rates are supplied explicitly:

```bash
python -m evals.run --mode live --confirm-live --scenario fee-exact-match \
  --input-cost-per-million 3.00 --output-cost-per-million 15.00
```

No pricing is hardcoded. Prices change, differ by model and tier, and a guessed
rate baked into a repository becomes a confidently wrong number that outlives
whoever guessed it. Without rates *or* observable tokens: `NOT CALCULATED`.

## Running

```bash
python -m evals.run --mode offline
```

Free, offline, no credentials. This is the default and the only mode that can
run by accident.

```bash
python -m evals.run --validate-only
python -m evals.run --list
```

### A single live scenario

```bash
python -m evals.run --mode live --scenario fee-exact-match --confirm-live
```

⚠️ **This spends real money.** Three independent things must line up — `--mode
live`, `--confirm-live`, and configured credentials — and the absence of any one
is an error rather than a quiet fallback to offline.

Safety mechanisms:

- offline is the default;
- `--confirm-live` is mandatory for live mode, and without it **no provider is
  even constructed**;
- missing credentials refuse the run rather than falling back;
- `--scenario` and `--limit` bound the spend;
- the selected count is printed **before** anything runs;
- an unknown scenario id is an error, not a silent full-suite run — in live mode
  a typo is the difference between one call and thirty-six;
- **there are no retries.** A retry loop is a cost multiplier hiding behind a
  transient failure.

`pytest` never makes a live call. There is a test asserting exactly that.

## Reports

Written to `eval-results/<run-id>.json` and `.md`. Generated results are
gitignored — committing metrics invites presenting a favourable run as a
standing claim.

## Interpreting results

Read in this order:

1. **Ungrounded results.** Non-zero on a live run means fabrication. Nothing
   else matters until it is zero.
2. **The unsafe escalation cell.** Cases needing a human routed as findings.
3. **Adversarial-band accuracy.** The `ADVERSARIAL` scenarios separate reasoning
   from pattern matching; aggregate accuracy hides them.
4. **Strict vs acceptable accuracy.** A wide gap means the model is reaching
   defensible-but-not-best conclusions.
5. **Evidence coverage.** Low coverage with high grounding means true but thin.

## Limitations

**Synthetic scenarios do not prove production performance.** These 36 cases are
invented. They are useful for regression detection and controlled comparison —
the same scenarios, the same guardrail, a different model or prompt — and they
are not a claim about behaviour on real financial data, which is messier,
higher-volume, and distributed differently.

Also outside what this measures:

- prose claims in `rootCause` that cite nothing;
- hidden reasoning (never requested, never inspected);
- retrieved evidence on **failed** runs — the agent returns its ledger only
  alongside a successful result, so rejected runs report
  `evidence_retrieved_available: false` rather than an empty ledger that would
  read as "nothing was retrieved";
- anything about latency or cost at production volume.

A benchmark is a proxy. Treat a good score as the absence of a specific known
failure, not as evidence of quality.
