import type { Evidence, EvidenceSourceType } from "../types";

/**
 * The evidence a recommendation actually rests on.
 *
 * Every item here passed the backend's grounding validator before it was
 * stored — a result citing anything unverifiable is rejected whole and never
 * persisted. So this list answers "what did the model actually see?" rather
 * than "what did it claim to see", and nothing is added to it here.
 */

const GROUP_ORDER: EvidenceSourceType[] = [
  "TRANSACTION",
  "SETTLEMENT",
  "FEE_RULE",
  "POLICY_DOCUMENT",
];

const GROUP_LABEL: Record<EvidenceSourceType, string> = {
  TRANSACTION: "Transactions",
  SETTLEMENT: "Settlements",
  FEE_RULE: "Fee rules",
  POLICY_DOCUMENT: "Policy documents",
};

const GROUP_NOTE: Record<EvidenceSourceType, string> = {
  TRANSACTION: "Authoritative records held by the Financial Core.",
  SETTLEMENT: "Processor-reported settlement records.",
  FEE_RULE:
    "Fee configuration. A rule matching an amount is context, not proof that it was charged.",
  POLICY_DOCUMENT: "Excerpts stored as retrieved, so they cannot drift.",
};

export function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  if (evidence.length === 0) {
    return (
      <p className="section-note">
        This conclusion cites no evidence. The guardrail escalates such results
        rather than routing them for approval.
      </p>
    );
  }

  const grouped = GROUP_ORDER.map((source) => ({
    source,
    items: evidence.filter((item) => item.source_type === source),
  })).filter((group) => group.items.length > 0);

  return (
    <div>
      {grouped.map(({ source, items }) => (
        <section className="evidence-group" key={source}>
          <h3 className="evidence-group-head">
            {GROUP_LABEL[source]}
            <span className="badge neutral">{items.length}</span>
          </h3>
          <p className="section-note">{GROUP_NOTE[source]}</p>
          {items.map((item, index) => (
            <article className="evidence-item" key={`${item.reference}-${item.section ?? index}`}>
              <div className="evidence-ref">{item.reference}</div>
              {item.section && <div className="evidence-section">§ {item.section}</div>}
              {item.excerpt && <blockquote className="evidence-excerpt">{item.excerpt}</blockquote>}
            </article>
          ))}
        </section>
      ))}
    </div>
  );
}
