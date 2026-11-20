import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { formatLetterDate } from "./document-table";
import { ErrorAlert } from "./page";

type Task = components["schemas"]["TaskOut"];

const day = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium" });

/** Ticks a task off (`done`) or opens it again; the document and its history reload. */
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
  tasks,
  canEdit,
}: {
  workspaceId: string;
  tasks: Task[];
  canEdit: boolean;
}) {
  const tick = useTick(workspaceId);
  return (
    <>
      <ul className="task-list">
        {tasks.map((task) => {
          const done = task.status === "done";
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
              {task.created_by_ai ? (
                <span className="muted small">{t`Automatisch door AI`}</span>
              ) : null}
            </li>
          );
        })}
      </ul>
      <ErrorAlert error={tick.error} />
    </>
  );
}
