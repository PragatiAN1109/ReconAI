import type { AuditEvent } from "../types";
import { formatTimestamp } from "../utils/datetime";
import { Badge } from "./Badge";
import { actorTone, describeAuditEvent, humanise } from "../utils/workflow";

/**
 * The append-only audit trail, oldest first.
 *
 * Rendered from persisted records only. The backend stores no prompt, provider
 * payload or model reasoning, so there is nothing of that kind here to leak —
 * the metadata shown is the small summary it writes deliberately.
 *
 * ``actor_type`` is given prominence because "who did this" is the question an
 * audit trail exists to answer: SYSTEM for deterministic transitions, AI for
 * the proposal, HUMAN for the decision.
 */

/** Metadata keys worth surfacing, in a fixed order. */
const SHOWN_KEYS = [
  "classification",
  "confidence",
  "evidence_count",
  "decision",
  "resulting_status",
  "confidence_threshold",
  "minimum_evidence",
  "recommendation_id",
  "review_id",
  "model_name",
  "prompt_version",
  "failure_type",
] as const;

function metaChips(metadata: Record<string, unknown> | null): string[] {
  if (!metadata) return [];
  const chips: string[] = [];
  for (const key of SHOWN_KEYS) {
    const value = metadata[key];
    if (value === undefined || value === null) continue;
    chips.push(`${key}: ${String(value)}`);
  }
  return chips;
}

export function AuditTimeline({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) {
    return <p className="section-note">No audit events recorded yet.</p>;
  }

  return (
    <ol className="timeline">
      {events.map((event) => {
        const tone = actorTone(event.actor_type);
        const reason = event.metadata?.["reason"];
        return (
          <li className="timeline-item" key={event.event_id}>
            <span className={`timeline-dot ${tone}`} aria-hidden="true" />
            <div className="timeline-head">
              <span className="timeline-type">{humanise(event.event_type)}</span>
              <Badge tone={tone} label={event.actor_type} />
              {event.actor_id && <span className="mono">{event.actor_id}</span>}
              <time className="timeline-time" dateTime={event.occurred_at}>
                {formatTimestamp(event.occurred_at)}
              </time>
            </div>
            <div className="timeline-desc">{describeAuditEvent(event)}</div>
            {typeof reason === "string" && <div className="timeline-desc">{reason}</div>}
            {metaChips(event.metadata).length > 0 && (
              <div className="timeline-meta">
                {metaChips(event.metadata).map((chip) => (
                  <span className="meta-chip" key={chip}>
                    {chip}
                  </span>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
