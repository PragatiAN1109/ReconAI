import type { Tone } from "../utils/workflow";
import { humanise } from "../utils/workflow";

/** A workflow-state chip. Colour carries meaning; nothing else here is coloured. */
export function Badge({ tone, label }: { tone: Tone; label: string }) {
  return <span className={`badge ${tone}`}>{humanise(label)}</span>;
}
