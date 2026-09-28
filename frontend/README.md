# ReconAI — Operations Console

A React console for investigating reconciliation exceptions.

Two screens: an exception queue, and an investigation detail view. All data is
read from the running backends; nothing here is mocked or hardcoded.

## Why the UI is shaped this way

The product's claim is a separation of authority:

```
DETERMINISTIC EXCEPTION → AI INVESTIGATION → DETERMINISTIC GUARDRAIL → HUMAN REVIEW
```

The console makes that visible rather than asserting it. Every section carries
an origin tag — *Deterministic fact*, *AI-generated · advisory*, *Deterministic
policy*, *Human authority* — because an operator who cannot tell a model's
proposal from a financial fact is the exact failure this system exists to
prevent.

Three consequences worth knowing:

- **The AI section is advisory and says so.** A classification is a proposal,
  never a determination.
- **Approval is not a ledger change.** Recording a decision writes only to the
  Investigation Service. The reconciliation exception stays as it was, and the
  console issues no write to the Financial Core at all.
- **Escalation is not failure.** It is coloured as a caution, not an error: the
  guardrail declining to present a weak explanation as a finding is the system
  working.

## Running

Both backends must be running first — see `backend/README.md` and
`agent-service/README.md`.

```bash
npm install && npm run dev
```

Then open <http://localhost:5173>.

### Why a dev proxy

Neither backend sends CORS headers, and a UI phase is no reason to change
backend configuration. The Vite dev server proxies instead, so the browser makes
only same-origin requests:

| Browser path | Service |
|---|---|
| `/api/core/*` | Spring Financial Core, `:8099` |
| `/api/investigation/*` | Python Investigation Service, `:8000` |

Two prefixes rather than one, because there genuinely are two services with two
owners. Collapsing them would hide a boundary the product depends on.

A production deployment would need either CORS on the services or a reverse
proxy in front of both. That is a deployment decision and is out of scope here.

## Scripts

```bash
npm run dev     # dev server with proxy
npm run build   # typecheck + production build
npm test        # vitest
```

## Layout

```
src/
  api/         one place HTTP happens; typed errors
  components/  Badge, Evidence, AuditTimeline, ReviewPanel, state views
  pages/       DashboardPage, InvestigationDetailPage
  types/       mirrors the real backend DTOs
  utils/       money (no floating point), workflow rules, dates
```

No state library. Two screens of server data do not need one; `useState` plus a
re-fetch after each mutation is the whole model, and it has the useful property
that what is on screen came from the backend rather than from optimism.

## Money

Amounts stay decimal **strings** from response to screen. `parseFloat("2500.10")`
is not 2500.10, and a reconciliation console whose figures disagree with the
ledger by a cent is worse than one that shows nothing. Where arithmetic is
unavoidable it uses integer minor units via `BigInt`; where the backend already
computed a value, that value is displayed rather than recomputed.

## Known limitations

- **No classification or confidence in the queue.** The list endpoint does not
  return them, and fetching a recommendation per row would be an N+1 of requests
  that mostly 404 — a PENDING investigation genuinely has no conclusion. Adding
  them would need a backend change, which this phase does not make.
- **The recorded decision is reconstructed from the audit trail.** There is no
  endpoint to fetch a review, so the console reads the review event instead of
  inventing a route. It therefore shows who, when and what, but not the
  reviewer's free-text comment.
- **No authentication.** `reviewed_by` is typed in and recorded unverified. The
  UI labels it as demo attribution wherever it appears.
- **No live updates.** A RUNNING investigation needs a manual refresh; polling
  or streaming was out of scope.
