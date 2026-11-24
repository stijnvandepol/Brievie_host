import { plural, t } from "@lingui/core/macro";
import { dateFormat } from "./format";
import { formattingLocale } from "./i18n";

/**
 * Words for a task in "Mijn taken" (spec S 6): when it is due, the heading of its group,
 * what it costs and for whom. The server counts the days (Europe/Amsterdam); these only
 * put them into words.
 */

const shortDay = dateFormat({ day: "numeric", month: "short" });

function day(deadline: string): string {
  // Noon, so no time zone moves the day.
  return shortDay.format(new Date(`${deadline}T12:00:00`));
}

/** "Vandaag", "Morgen", "Nog 5 dagen (1 dec)", "3 dagen te laat (18 nov)"; "" without a date. */
export function whenText(daysLeft: number | null | undefined, deadline: string | null): string {
  if (deadline === null || daysLeft === null || daysLeft === undefined) return "";
  const date = day(deadline);
  if (daysLeft === 0) return t`Vandaag`;
  if (daysLeft === 1) return t`Morgen`;
  if (daysLeft > 1) return t`Nog ${daysLeft} dagen (${date})`;
  const late = -daysLeft;
  return plural(late, { one: `# dag te laat (${date})`, other: `# dagen te laat (${date})` });
}

export type TaskGroup = "overdue" | "within_week" | "later" | "no_date" | "waiting" | "snoozed";

export const GROUPS: readonly TaskGroup[] = ["overdue", "within_week", "later", "no_date"];

/** Today in the Netherlands as `2026-11-24`, the day the server counts from. */
export function amsterdamToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Amsterdam" }).format(now);
}

/** `day` plus `days` days, as `YYYY-MM-DD` (noon, so no time zone moves the day). */
export function addDays(day: string, days: number): string {
  const moved = new Date(`${day}T12:00:00Z`);
  moved.setUTCDate(moved.getUTCDate() + days);
  return moved.toISOString().slice(0, 10);
}

export function groupTitle(group: TaskGroup): string {
  switch (group) {
    case "overdue":
      return t`Te laat`;
    case "within_week":
      return t`Binnen 7 dagen`;
    case "later":
      return t`Later`;
    case "no_date":
      return t`Zonder datum`;
    case "waiting":
      return t`Wacht op reactie`;
    case "snoozed":
      return t`Uitgesteld`;
  }
}

/** "€ 1.234,56" from the API's decimal string; "" when unknown. */
export function amountText(amount: string): string {
  if (!amount) return "";
  const value = Number(amount);
  if (!Number.isFinite(value)) return "";
  return new Intl.NumberFormat(formattingLocale(), { style: "currency", currency: "EUR" }).format(
    value,
  );
}

function names(list: string[]): string {
  if (list.length <= 1) return list.join("");
  const last = list[list.length - 1] ?? "";
  const rest = list.slice(0, -1).join(", ");
  return t`${rest} en ${last}`;
}

export type ForFacts = {
  reason: string;
  /** The letter's persons and departments other than the reader's own. */
  for_names: string[];
  assigned_to_name: string;
  assigned_by_name: string;
};

/** Whose task it is, in words: "Voor jou en Piet", "Voor Finance", "Zonder naam",
 * "Aan jou gegeven door Anne", "Bij Piet". `me` is the reader's own name. */
export function forText(task: ForFacts, me: string): string {
  const others = names(task.for_names);
  switch (task.reason) {
    case "assigned":
      if (!task.assigned_by_name || task.assigned_by_name === me) return t`Jij doet dit`;
      return t`Aan jou gegeven door ${task.assigned_by_name}`;
    case "addressee":
      return others ? t`Voor jou en ${others}` : t`Voor jou`;
    case "unassigned":
      return others ? t`Voor ${others}` : t`Zonder naam`;
    default:
      if (task.assigned_to_name) return t`Bij ${task.assigned_to_name}`;
      return others ? t`Voor ${others}` : "";
  }
}
