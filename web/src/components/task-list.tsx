import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { amountText } from "../task-time";
import { formatLetterDate } from "./document-table";
import { ErrorAlert } from "./page";
import { TaskActions } from "./task-actions";

type Task = components["schemas"]["TaskOut"];

const day = dateFormat({ dateStyle: "medium" });

/** Ticks a task off (`done`) or opens it again; the document, its history and "Mijn taken"
 * reload (all under the documents key). */
function useTick(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ taskId, done }: { taskId: string; done: boolean }) => {
      const params = { path: { workspace_id: workspaceId, task_id: taskId } };
      const { data, error } = done
        ? await api.POST("/api/v1/workspaces/{workspace_id}/tasks/{task_id}/complete", { params })
        : await api.POST("/api/v1/workspaces/{workspace_id}/tasks/{task_id}/reopen", { params });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
  });
}

/** The tasks of one document, each with a checkbox named after its description; a task the
 * AI made while confirming the letter says so. */
export function TaskList({
  workspaceId,
  documentId,
  tasks,
  canEdit,
}: {
  workspaceId: string;
  /** The letter the tasks are on: they can be given to someone who may read it. */
  documentId: string;
  tasks: Task[];
  canEdit: boolean;
}) {
  const tick = useTick(workspaceId);
  return (
    <>
      <ul className="task-list">
        {tasks.map((task) => {
          const done = task.status === "done";
          const assignee = task.assigned_to_name ?? "";
          return (
            <li key={task.id}>
              <label className={done ? "task-line task-done" : "task-line"}>
                <input
                  type="checkbox"
                  checked={done}
                  disabled={!canEdit || tick.isPending}
                  onChange={(event) =>
                    tick.mutate({ taskId: task.id, done: event.currentTarget.checked })
                  }
                />
                <span>{task.description}</span>
              </label>
              {done && task.completed_at ? (
                <span className="muted small">
                  {t`Afgerond op ${day.format(new Date(task.completed_at))}`}
                </span>
              ) : task.deadline ? (
                <span className="muted small">
                  {t`Uiterlijk ${formatLetterDate(task.deadline)}`}
                </span>
              ) : null}
              {task.amount ? (
                <span className="muted small task-amount">{amountText(task.amount)}</span>
              ) : null}
              {task.assigned_to_name ? (
                <span className="muted small">{t`Aan ${assignee} gegeven`}</span>
              ) : null}
              {task.created_by_ai ? (
                <span className="muted small">{t`Automatisch door AI`}</span>
              ) : null}
              {canEdit && !done ? (
                <span className="task-actions">
                  <TaskActions
                    workspaceId={workspaceId}
                    task={{ ...task, document_id: documentId }}
                  />
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
      <ErrorAlert error={tick.error} />
    </>
  );
}
