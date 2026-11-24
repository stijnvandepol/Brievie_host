import { plural, t } from "@lingui/core/macro";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { Badge } from "./ui";

export type LetterCounts = components["schemas"]["LetterStatusCountsOut"];

/** The values of the URL filter ("status") and the status they ask the server for. */
export const STATUS_FILTERS = [
  { url: "nieuw", api: "new" },
  { url: "openstaand", api: "open" },
  { url: "wacht", api: "waiting" },
  { url: "afgehandeld", api: "handled" },
] as const;

export type StatusFilter = (typeof STATUS_FILTERS)[number]["url"];

export function apiStatus(filter: StatusFilter | undefined): string | undefined {
  return STATUS_FILTERS.find((entry) => entry.url === filter)?.api;
}

const shortDay = dateFormat({ day: "numeric", month: "short" });

/** A date-only value as "3 dec", without a time zone moving the day. */
export function shortDate(value: string): string {
  return shortDay.format(new Date(`${value}T12:00:00`));
}

/** The words of a letter's status. */
export function letterStatusWord(status: string): string {
  switch (status) {
    case "new":
      return t`Nieuw`;
    case "open":
      return t`Openstaand`;
    case "waiting":
      return t`Wacht op reactie`;
    case "handled":
      return t`Afgehandeld`;
    default:
      return "";
  }
}

/**
 * The status of a letter and, when it is late, the marker "Te laat": text in a tag, never
 * colour alone. "Nieuw" is the letter's own status (not checked yet), not the per-person
 * "Ongelezen". Nothing for a server that sends no status.
 */
export function LetterStatusBadge({
  status,
  overdue = false,
  waitingUntil,
}: {
  status: string | undefined;
  overdue?: boolean;
  waitingUntil?: string | null;
}) {
  if (!status) return null;
  const until = waitingUntil ? shortDate(waitingUntil) : "";
  return (
    <>
      {status === "new" ? <Badge intent="info">{t`Nieuw`}</Badge> : null}
      {status === "open" ? <Badge>{t`Openstaand`}</Badge> : null}
      {status === "waiting" ? (
        <Badge>{until ? t`Wacht op reactie tot ${until}` : t`Wacht op reactie`}</Badge>
      ) : null}
      {status === "handled" ? <Badge intent="success">{t`Afgehandeld`}</Badge> : null}
      {overdue ? <Badge intent="warning">{t`Te laat`}</Badge> : null}
    </>
  );
}

/** "2 nieuw, 3 openstaand, 1 te laat, 2 wachten op reactie": only the numbers above zero. */
export function letterCountsText(counts: LetterCounts): string {
  const fresh = counts.new;
  const open = counts.open;
  const late = counts.overdue;
  const waiting = counts.waiting;
  const parts: string[] = [];
  if (fresh > 0) parts.push(t`${fresh} nieuw`);
  if (open > 0) parts.push(t`${open} openstaand`);
  if (late > 0) parts.push(t`${late} te laat`);
  if (waiting > 0) {
    parts.push(plural(waiting, { one: "# wacht op reactie", other: "# wachten op reactie" }));
  }
  return parts.join(", ");
}
