import { t } from "@lingui/core/macro";
import { amsterdamToday } from "../task-time";
import { shortDate } from "./letter-status";
import { Badge } from "./ui";

export type WaitFacts = {
  deadline: string | null;
  waiting_until?: string | null;
  wait_over?: boolean;
  snoozed_until?: string | null;
};

/**
 * What a task says about waiting: "Wacht op reactie tot 3 dec" (shared; the deadline stays
 * visible in grey, not as a late marker), "Wachten voorbij sinds 3 dec" once the day has
 * arrived (until somebody acts), and the caller's own "Uitgesteld tot 1 dec".
 */
export function TaskWaitInfo({ task }: { task: WaitFacts }) {
  const waiting = task.waiting_until ?? null;
  const snoozed = task.snoozed_until ?? null;
  if (!waiting && !snoozed) return null;
  const waitDay = waiting ? shortDate(waiting) : "";
  const snoozeDay = snoozed ? shortDate(snoozed) : "";
  const pastTerm = waiting !== null && task.deadline !== null && task.deadline < amsterdamToday();
  const term = task.deadline ? shortDate(task.deadline) : "";
  return (
    <p className="task-wait">
      {waiting && task.wait_over ? (
        <Badge intent="warning">{t`Wachten voorbij sinds ${waitDay}`}</Badge>
      ) : null}
      {waiting && !task.wait_over ? <Badge>{t`Wacht op reactie tot ${waitDay}`}</Badge> : null}
      {snoozed ? <Badge>{t`Uitgesteld tot ${snoozeDay}`}</Badge> : null}
      {pastTerm && !task.wait_over ? (
        <span className="muted small">{t`termijn was ${term}`}</span>
      ) : null}
    </p>
  );
}
