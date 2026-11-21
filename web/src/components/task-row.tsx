import { t } from "@lingui/core/macro";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import type { components } from "../api/schema";
import { amountText, forText, whenText } from "../task-time";
import { Badge } from "./ui";

export type TaskItem = components["schemas"]["TaskItemOut"];

/** What the row says about where the date came from (spec S 6 item 6). */
function whyText(task: TaskItem): string {
  if (task.deadline && task.deadline_source === "in_document" && task.deadline_quote) {
    const quote = task.deadline_quote;
    return t`Uit de brief: ‘${quote}’`;
  }
  if (task.deadline && task.deadline_source === "manual") return t`Datum zelf ingevuld`;
  return "";
}

/**
 * One task of "Mijn taken", enough to act on without opening the letter: a tick box named
 * after the action, when, what it costs, from whom and for whom, why that date, and
 * whether a date is still needed (spec S 6).
 */
export function TaskRow({
  workspaceId,
  task,
  me,
  ticked,
  canTick,
  onTick,
  actions,
}: {
  workspaceId: string;
  task: TaskItem;
  /** The reader's own name, to say "Jij doet dit". */
  me: string;
  /** Ticked off on this page (it stays, struck through, until the page is left). */
  ticked: boolean;
  canTick: boolean;
  onTick: (done: boolean) => void;
  actions?: ReactNode;
}) {
  const description = task.description;
  const when = whenText(task.days_left ?? null, task.deadline);
  const late = (task.days_left ?? 0) < 0;
  const amount = amountText(task.amount ?? "");
  const sender = task.sender;
  const whose = forText(
    {
      reason: task.reason ?? "",
      for_names: task.for_names ?? [],
      assigned_to_name: task.assigned_to_name ?? "",
      assigned_by_name: task.assigned_by_name ?? "",
    },
    me,
  );
  const fromFor = [sender ? t`Van ${sender}` : "", whose].filter(Boolean).join(" · ");
  const why = whyText(task);
  const term = task.term_quote ?? "";
  return (
    <li className={ticked ? "task-row task-done" : "task-row"}>
      <input
        type="checkbox"
        className="task-check"
        aria-label={t`Klaar: ${description}`}
        checked={ticked}
        disabled={!canTick}
        onChange={(event) => onTick(event.currentTarget.checked)}
      />
      <div className="task-body">
        <p className="task-action">{description}</p>
        {when || amount ? (
          <p className="task-when">
            {when ? <span className={late ? "task-late" : undefined}>{when}</span> : null}
            {when && amount ? " · " : null}
            {amount ? <span className="task-amount">{amount}</span> : null}
          </p>
        ) : null}
        {fromFor ? <p className="muted small">{fromFor}</p> : null}
        {why ? <p className="muted small">{why}</p> : null}
        {task.needs_deadline ? (
          <div className="task-needs-date">
            <Badge intent="warning">{t`Datum nodig`}</Badge>
            <p className="small">{t`De brief noemt een termijn: ‘${term}’. Vul de datum in.`}</p>
          </div>
        ) : null}
        <div className="task-actions">
          {actions}
          {task.document_id ? (
            <Link
              to="/w/$workspaceId/post/$documentId"
              params={{ workspaceId, documentId: task.document_id }}
              className="task-letter-link"
              aria-label={t`Brief bekijken: ${description}`}
            >
              {t`Brief bekijken`}
            </Link>
          ) : null}
        </div>
      </div>
    </li>
  );
}
