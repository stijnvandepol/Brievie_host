import { t } from "@lingui/core/macro";
import type { components } from "../api/schema";

type Label = components["schemas"]["LabelOut"];

/** The palette of the API, in the order the colour picker shows it. */
export const LABEL_COLORS = [
  "grey",
  "blue",
  "teal",
  "green",
  "yellow",
  "orange",
  "red",
  "purple",
] as const;

export function colorName(color: (typeof LABEL_COLORS)[number]): string {
  switch (color) {
    case "grey":
      return t`Grijs`;
    case "blue":
      return t`Blauw`;
    case "teal":
      return t`Groenblauw`;
    case "green":
      return t`Groen`;
    case "yellow":
      return t`Geel`;
    case "orange":
      return t`Oranje`;
    case "red":
      return t`Rood`;
    case "purple":
      return t`Paars`;
  }
}

/** A key from a later palette shows as grey. */
function colorClass(color: string): string {
  return `label-${LABEL_COLORS.find((known) => known === color) ?? "grey"}`;
}

/** A label: always its name as text, the colour is only a second signal. */
export function LabelChip({ label }: { label: Pick<Label, "name" | "color"> }) {
  return <span className={`label-chip ${colorClass(label.color)}`}>{label.name}</span>;
}

/** The chips of a list of ids, in the order of the ids; ids nobody knows are skipped. */
export function LabelChips({ labels, ids }: { labels: Label[]; ids: string[] }) {
  const known = ids.flatMap((id) => labels.filter((label) => label.id === id));
  if (known.length === 0) return null;
  return (
    <span className="label-chips">
      {known.map((label) => (
        <LabelChip key={label.id} label={label} />
      ))}
    </span>
  );
}
