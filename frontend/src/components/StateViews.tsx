import { ApiError, NetworkError } from "../api/client";

/**
 * Loading, empty and error presentation.
 *
 * Errors are never collapsed into "something went wrong". An operator needs to
 * know whether a service is down, a record is missing, or the request was
 * refused — those imply different actions.
 */

export function LoadingPanel({ label }: { label: string }) {
  return (
    <div className="state-panel" role="status" aria-live="polite">
      <div className="state-title">{label}</div>
      <div className="state-detail">Contacting ReconAI services…</div>
    </div>
  );
}

export function SkeletonRows({ rows = 5, columns = 6 }: { rows?: number; columns?: number }) {
  return (
    <tbody aria-hidden="true">
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r}>
          {Array.from({ length: columns }, (_, c) => (
            <td key={c}>
              <div className="skeleton" style={{ width: `${50 + ((r + c) % 4) * 12}%` }} />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

export function EmptyPanel({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="state-panel">
      <div className="state-title">{title}</div>
      <div className="state-detail">{detail}</div>
    </div>
  );
}

/** Turn an unknown thrown value into something an operator can act on. */
export function describeError(error: unknown): { title: string; detail: string } {
  if (error instanceof NetworkError) {
    return {
      title: "Service unreachable",
      detail:
        "A ReconAI backend did not respond. Check that the Financial Core and the " +
        "Investigation Service are running, then retry.",
    };
  }
  if (error instanceof ApiError) {
    if (error.isNotFound) {
      return { title: "Not found", detail: error.detail };
    }
    if (error.isConflict) {
      return { title: "State conflict", detail: error.detail };
    }
    return { title: `Request failed (${error.status})`, detail: error.detail };
  }
  if (error instanceof Error) {
    return { title: "Unexpected error", detail: error.message };
  }
  return { title: "Unexpected error", detail: String(error) };
}

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { title, detail } = describeError(error);
  return (
    <div className="state-panel" role="alert">
      <div className="state-title">{title}</div>
      <div className="state-detail">{detail}</div>
      {onRetry && (
        <div style={{ marginTop: 16 }}>
          <button className="btn" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

export function Notice({
  tone,
  title,
  children,
}: {
  tone: "error" | "warning" | "info";
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={`notice ${tone}`} role={tone === "error" ? "alert" : undefined}>
      <div className="notice-title">{title}</div>
      {children}
    </div>
  );
}
