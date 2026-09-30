import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { ExceptionType, Investigation, InvestigationStatus } from "../types";
import { listInvestigations } from "../api/investigations";
import { formatShort } from "../utils/datetime";
import { countByStatus, humanise, statusTone } from "../utils/workflow";
import { Badge } from "../components/Badge";
import { RunReconciliationModal } from "../components/RunReconciliationModal";
import { EmptyPanel, ErrorPanel, SkeletonRows } from "../components/StateViews";

/**
 * The exception queue.
 *
 * Counts are derived in the browser from the list the backend already returns,
 * rather than asking for an aggregation endpoint that does not exist. With one
 * row per detected discrepancy that is the right trade: a new endpoint would be
 * backend scope creep for arithmetic the client can do.
 *
 * Classification and confidence are deliberately absent from this table. The
 * list endpoint does not carry them, and fetching a recommendation per row
 * would mean an N+1 of requests — most of which 404, because a PENDING
 * investigation genuinely has no conclusion. That limitation is documented
 * rather than papered over.
 */

const STATUS_ORDER: InvestigationStatus[] = [
  "PENDING",
  "RUNNING",
  "AWAITING_REVIEW",
  "ESCALATED",
  "COMPLETED",
  "FAILED",
];

export function DashboardPage() {
  const navigate = useNavigate();
  const [investigations, setInvestigations] = useState<Investigation[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [statusFilter, setStatusFilter] = useState<InvestigationStatus | "ALL">("ALL");
  const [typeFilter, setTypeFilter] = useState<ExceptionType | "ALL">("ALL");
  const [query, setQuery] = useState("");
  const [playgroundOpen, setPlaygroundOpen] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setInvestigations(null);
    try {
      const response = await listInvestigations();
      setInvestigations(response.items);
    } catch (caught) {
      setError(caught);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(
    () => countByStatus((investigations ?? []).map((item) => item.status)),
    [investigations],
  );

  const exceptionTypes = useMemo(() => {
    const seen = new Set<ExceptionType>();
    for (const item of investigations ?? []) seen.add(item.exception_type);
    return [...seen].sort();
  }, [investigations]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (investigations ?? []).filter((item) => {
      if (statusFilter !== "ALL" && item.status !== statusFilter) return false;
      if (typeFilter !== "ALL" && item.exception_type !== typeFilter) return false;
      if (!needle) return true;
      return (
        item.investigation_id.toLowerCase().includes(needle) ||
        item.exception_id.toLowerCase().includes(needle) ||
        item.transaction_id.toLowerCase().includes(needle)
      );
    });
  }, [investigations, statusFilter, typeFilter, query]);

  if (error !== null) {
    return (
      <div className="page">
        <Header total={null} onRunReconciliation={() => setPlaygroundOpen(true)} />
        <ErrorPanel error={error} onRetry={() => void load()} />
      </div>
    );
  }

  return (
    <div className="page">
      <Header
        total={investigations?.length ?? null}
        onRunReconciliation={() => setPlaygroundOpen(true)}
      />

      {playgroundOpen && (
        <RunReconciliationModal
          onClose={() => {
            setPlaygroundOpen(false);
            // A run may have created an investigation. Reload so the queue
            // reflects it without the visitor having to refresh the page.
            void load();
          }}
        />
      )}

      <div className="metrics">
        <button
          type="button"
          className={`metric${statusFilter === "ALL" ? " is-active" : ""}`}
          onClick={() => setStatusFilter("ALL")}
        >
          <div className="metric-value">{investigations?.length ?? "—"}</div>
          <div className="metric-label">Total</div>
        </button>
        {STATUS_ORDER.map((status) => (
          <button
            type="button"
            key={status}
            className={`metric${statusFilter === status ? " is-active" : ""}`}
            onClick={() => setStatusFilter(statusFilter === status ? "ALL" : status)}
          >
            <div className="metric-value">{investigations ? counts[status] : "—"}</div>
            <div className="metric-label">{humanise(status)}</div>
          </button>
        ))}
      </div>

      <div className="toolbar">
        <select
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value as InvestigationStatus | "ALL")}
          aria-label="Filter by status"
        >
          <option value="ALL">All statuses</option>
          {STATUS_ORDER.map((status) => (
            <option key={status} value={status}>
              {humanise(status)}
            </option>
          ))}
        </select>

        <select
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value as ExceptionType | "ALL")}
          aria-label="Filter by exception type"
          disabled={exceptionTypes.length === 0}
        >
          <option value="ALL">All exception types</option>
          {exceptionTypes.map((type) => (
            <option key={type} value={type}>
              {humanise(type)}
            </option>
          ))}
        </select>

        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search investigation, exception or transaction"
          aria-label="Search"
          style={{ minWidth: 300 }}
        />

        <div className="toolbar-spacer" />
        <span className="result-count">
          {investigations ? `${visible.length} of ${investigations.length}` : "Loading…"}
        </span>
        <button className="btn ghost" onClick={() => void load()}>
          Refresh
        </button>
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Investigation</th>
              <th>Exception</th>
              <th>Transaction</th>
              <th>Detected discrepancy</th>
              <th>Workflow status</th>
              <th>Detected</th>
              <th>Updated</th>
            </tr>
          </thead>
          {investigations === null ? (
            <SkeletonRows rows={5} columns={7} />
          ) : (
            <tbody>
              {visible.map((item) => (
                <tr
                  key={item.investigation_id}
                  className="clickable"
                  tabIndex={0}
                  role="link"
                  onClick={() => navigate(`/investigations/${item.investigation_id}`)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      navigate(`/investigations/${item.investigation_id}`);
                    }
                  }}
                >
                  <td className="mono">{item.investigation_id}</td>
                  <td className="mono">{item.exception_id}</td>
                  <td className="mono">{item.transaction_id}</td>
                  <td>
                    <Badge tone="neutral" label={item.exception_type} />
                  </td>
                  <td>
                    <Badge tone={statusTone(item.status)} label={item.status} />
                  </td>
                  <td>{formatShort(item.detected_at)}</td>
                  <td>{formatShort(item.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          )}
        </table>

        {investigations !== null && visible.length === 0 && (
          <EmptyPanel
            title={investigations.length === 0 ? "No investigations yet" : "No matching investigations"}
            detail={
              investigations.length === 0
                ? "Investigations appear here when the Financial Core detects a reconciliation discrepancy and publishes it. Nothing can be created from this console."
                : "No investigation matches the current filters."
            }
          />
        )}
      </div>
    </div>
  );
}

function Header({
  total,
  onRunReconciliation,
}: {
  total: number | null;
  onRunReconciliation: () => void;
}) {
  return (
    <div className="page-head page-head-row">
      <div>
        <h1 className="page-title">Exception queue</h1>
        <p className="page-subtitle">
          Discrepancies detected deterministically by the Financial Core.
          {total !== null && ` ${total} investigation${total === 1 ? "" : "s"} on record.`}
        </p>
      </div>
      {/* In the header rather than the navigation: this is an action taken from
          the queue, not a separate area of the console. */}
      <button type="button" className="btn primary" onClick={onRunReconciliation}>
        + Run Reconciliation
      </button>
    </div>
  );
}
