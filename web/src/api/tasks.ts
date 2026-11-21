import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { ApiFailure, api } from "./client";

/** The key every "Mijn taken" list and count lives under (see `myTasksQuery`). */
export function myTasksKey(workspaceId: string) {
  return ["workspaces", workspaceId, "documents", "my-tasks"] as const;
}

/**
 * After a task changed: the letters (their task lists and history) and the counts are read
 * again. With `keepLists` the open lists stay as they are, so a ticked row stays in place
 * with "Ongedaan maken" until the page is left (spec S 6).
 */
export async function refreshAfterTask(
  queryClient: QueryClient,
  workspaceId: string,
  { keepLists = false }: { keepLists?: boolean } = {},
) {
  const tasks = myTasksKey(workspaceId);
  await queryClient.invalidateQueries({
    queryKey: ["workspaces", workspaceId, "documents"],
    predicate: (query) => {
      if (!keepLists) return true;
      const key = query.queryKey;
      const isTaskList = key[3] === tasks[3] && key[4] !== "counts";
      return !isTaskList;
    },
  });
}

/** Ticks a task off (`done`) or opens it again. */
export async function tickTask(workspaceId: string, taskId: string, done: boolean) {
  const params = { path: { workspace_id: workspaceId, task_id: taskId } };
  const { data, error } = done
    ? await api.POST("/api/v1/workspaces/{workspace_id}/tasks/{task_id}/complete", { params })
    : await api.POST("/api/v1/workspaces/{workspace_id}/tasks/{task_id}/reopen", { params });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

/** Gives the task to a member who can read its letter; `null` takes it back. */
export async function assignTask(workspaceId: string, taskId: string, userId: string | null) {
  const { data, error } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/tasks/{task_id}/assign",
    {
      params: { path: { workspace_id: workspaceId, task_id: taskId } },
      body: { user_id: userId },
    },
  );
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

/** "Datum invullen": the date the letter names. */
export async function setTaskDeadline(workspaceId: string, taskId: string, deadline: string) {
  const { data, error } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/tasks/{task_id}/deadline",
    {
      params: { path: { workspace_id: workspaceId, task_id: taskId } },
      body: { deadline },
    },
  );
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

/** The members a task can be given to: only those who may read its letter. */
export const taskAssigneesQuery = (workspaceId: string, taskId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", "my-tasks", "assignees", taskId],
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/tasks/{task_id}/assignees",
        { params: { path: { workspace_id: workspaceId, task_id: taskId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
