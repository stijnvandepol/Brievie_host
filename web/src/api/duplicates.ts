import type { QueryClient } from "@tanstack/react-query";
import { ApiFailure, api } from "./client";

/**
 * After a merge or a "two letters": everything that counts or lists letters, pairs, tasks
 * and the trash is read again. Search results are not cached.
 */
export async function refreshAfterDuplicate(queryClient: QueryClient, workspaceId: string) {
  await Promise.all(
    ["documents", "inbox-counts", "duplicates", "tasks", "trash", "persons", "labels"].map((key) =>
      queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, key] }),
    ),
  );
}

/** The two letters are one: `keepId` stays, the other goes to the trash. Answers the kept letter. */
export async function mergeDuplicate(
  workspaceId: string,
  duplicateId: string,
  keepId: string,
  completeTwinTasks: boolean,
) {
  const { data, error } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/duplicates/{duplicate_id}/merge",
    {
      params: { path: { workspace_id: workspaceId, duplicate_id: duplicateId } },
      body: { keep_document_id: keepId, complete_twin_tasks: completeTwinTasks },
    },
  );
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

/** The two letters are two letters: the pair is never proposed again. */
export async function dismissDuplicate(workspaceId: string, duplicateId: string) {
  const { error, response } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/duplicates/{duplicate_id}/dismiss",
    { params: { path: { workspace_id: workspaceId, duplicate_id: duplicateId } } },
  );
  if (!response.ok) throw new ApiFailure(error);
}
