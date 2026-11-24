import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "react-aria-components";
import { meQuery } from "../api/queries";
import { assignTask, refreshAfterTask, setTaskWaiting, snoozeTask } from "../api/tasks";
import { AssignDialog } from "./assign-dialog";
import { DeadlineDialog } from "./deadline-dialog";
import { ErrorAlert } from "./page";
import { SnoozeDialog } from "./snooze-dialog";
import { WaitingDialog } from "./waiting-dialog";

export type ActionFacts = {
  id: string;
  description: string;
  document_id?: string | null;
  deadline: string | null;
  term_quote?: string;
  assigned_to_id?: string | null;
  assigned_to_name?: string;
  /** What the server allows the caller (spec S 7.2): the client never works it out. */
  can_take?: boolean;
  can_hand_over?: boolean;
  can_release?: boolean;
  /** Letter status: waiting for a reply (shared), the day arrived, the caller's own snooze. */
  waiting_until?: string | null;
  wait_over?: boolean;
  snoozed_until?: string | null;
};

/**
 * What a member can do with a task besides ticking it: "Ik doe dit", "Niet meer aan mij"
 * (an admin on someone else's task: "Vrijgeven"), "Geef aan…" and, when the letter names
 * a term but no date is known, "Datum invullen". Who may change the assignment is the
 * server's answer per task (`can_take`, `can_hand_over`, `can_release`; navigation spec
 * S 7): "Ik doe dit" only on a task nobody has; someone else's task shows who does it.
 * Only for a task on a letter (one without a letter is not given to anyone, S 5.3).
 */
export function TaskActions({
  workspaceId,
  task,
  showTake = true,
  canEdit = true,
}: {
  workspaceId: string;
  task: ActionFacts;
  /** "Ik doe dit" here; "Op te pakken" shows its own. */
  showTake?: boolean;
  /** False for a viewer: only "Uitstellen…" (it changes nothing anybody else sees). */
  canEdit?: boolean;
}) {
  const queryClient = useQueryClient();
  const me = useQuery(meQuery).data;
  const [dialog, setDialog] = useState<"assign" | "deadline" | "waiting" | "snooze" | null>(null);
  const assign = useMutation({
    mutationFn: (userId: string | null) => assignTask(workspaceId, task.id, userId),
    onSettled: () => refreshAfterTask(queryClient, workspaceId),
  });
  const stopWaiting = useMutation({
    mutationFn: () => setTaskWaiting(workspaceId, task.id, null),
    onSettled: () => refreshAfterTask(queryClient, workspaceId),
  });
  const stopSnooze = useMutation({
    mutationFn: () => snoozeTask(workspaceId, task.id, null),
    onSettled: () => refreshAfterTask(queryClient, workspaceId),
  });
  const description = task.description;
  const mine = me !== undefined && me !== null && task.assigned_to_id === me.id;
  const term = task.term_quote ?? "";
  const needsDate = task.deadline === null && term !== "";
  // Someone else has it and the caller may not change that: say who, offer nothing.
  const takenBy = task.assigned_to_name ?? "";
  const taken =
    !mine &&
    Boolean(task.assigned_to_id) &&
    takenBy !== "" &&
    !task.can_release &&
    !task.can_hand_over;
  if (!canEdit) {
    return (
      <>
        {task.snoozed_until ? (
          <Button
            className="button-text"
            aria-label={t`Niet meer uitstellen: ${description}`}
            isDisabled={stopSnooze.isPending}
            onPress={() => stopSnooze.mutate()}
          >
            {t`Niet meer uitstellen`}
          </Button>
        ) : (
          <Button
            className="button-text"
            aria-label={t`Uitstellen: ${description}`}
            onPress={() => setDialog("snooze")}
          >
            {t`Uitstellen…`}
          </Button>
        )}
        <ErrorAlert error={stopSnooze.error} />
        <SnoozeDialog
          workspaceId={workspaceId}
          taskId={task.id}
          description={description}
          deadline={task.deadline}
          isOpen={dialog === "snooze"}
          onOpenChange={(open) => setDialog(open ? "snooze" : null)}
        />
      </>
    );
  }
  return (
    <>
      {needsDate ? (
        <Button
          className="button-text"
          aria-label={t`Datum invullen: ${description}`}
          onPress={() => setDialog("deadline")}
        >
          {t`Datum invullen`}
        </Button>
      ) : null}
      {task.document_id ? (
        <>
          {task.can_release ? (
            mine ? (
              <Button
                className="button-text"
                aria-label={t`Niet meer aan mij: ${description}`}
                isDisabled={assign.isPending}
                onPress={() => assign.mutate(null)}
              >
                {t`Niet meer aan mij`}
              </Button>
            ) : (
              <Button
                className="button-text"
                aria-label={t`Vrijgeven: ${description}`}
                isDisabled={assign.isPending}
                onPress={() => assign.mutate(null)}
              >
                {t`Vrijgeven`}
              </Button>
            )
          ) : null}
          {showTake && me && task.can_take ? (
            <Button
              className="button-text"
              aria-label={t`Ik doe dit: ${description}`}
              isDisabled={assign.isPending}
              onPress={() => assign.mutate(me.id)}
            >
              {t`Ik doe dit`}
            </Button>
          ) : null}
          {task.can_hand_over ? (
            <Button
              className="button-text"
              aria-label={t`Geef aan: ${description}`}
              onPress={() => setDialog("assign")}
            >
              {t`Geef aan…`}
            </Button>
          ) : null}
          {taken ? <span className="muted small">{t`Doet: ${takenBy}`}</span> : null}
        </>
      ) : null}
      {task.waiting_until ? (
        <Button
          className="button-text"
          aria-label={t`Niet meer wachten: ${description}`}
          isDisabled={stopWaiting.isPending}
          onPress={() => stopWaiting.mutate()}
        >
          {t`Niet meer wachten`}
        </Button>
      ) : (
        <Button
          className="button-text"
          aria-label={t`Wacht op reactie: ${description}`}
          onPress={() => setDialog("waiting")}
        >
          {t`Wacht op reactie…`}
        </Button>
      )}
      {task.snoozed_until ? (
        <Button
          className="button-text"
          aria-label={t`Niet meer uitstellen: ${description}`}
          isDisabled={stopSnooze.isPending}
          onPress={() => stopSnooze.mutate()}
        >
          {t`Niet meer uitstellen`}
        </Button>
      ) : (
        <Button
          className="button-text"
          aria-label={t`Uitstellen: ${description}`}
          onPress={() => setDialog("snooze")}
        >
          {t`Uitstellen…`}
        </Button>
      )}
      <ErrorAlert error={assign.error ?? stopWaiting.error ?? stopSnooze.error} />
      <WaitingDialog
        workspaceId={workspaceId}
        taskId={task.id}
        description={description}
        isOpen={dialog === "waiting"}
        onOpenChange={(open) => setDialog(open ? "waiting" : null)}
      />
      <SnoozeDialog
        workspaceId={workspaceId}
        taskId={task.id}
        description={description}
        deadline={task.deadline}
        isOpen={dialog === "snooze"}
        onOpenChange={(open) => setDialog(open ? "snooze" : null)}
      />
      <AssignDialog
        workspaceId={workspaceId}
        taskId={task.id}
        description={description}
        current={task.assigned_to_id ?? null}
        isOpen={dialog === "assign"}
        onOpenChange={(open) => setDialog(open ? "assign" : null)}
      />
      <DeadlineDialog
        workspaceId={workspaceId}
        taskId={task.id}
        description={description}
        term={term}
        isOpen={dialog === "deadline"}
        onOpenChange={(open) => setDialog(open ? "deadline" : null)}
      />
    </>
  );
}
