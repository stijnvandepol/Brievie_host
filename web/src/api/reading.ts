import type { QueryClient } from "@tanstack/react-query";
import { ApiFailure, api } from "./client";

/**
 * "Ongelezen" (spec S 5): the caller's own read state of a letter. Only an explicit call
 * marks a letter read: the letter page once it shows the letter, "Alles gelezen", never a
 * plain fetch.
 */

/** After a read mark changed: the lists and the menu's numbers, not the open letter. */
export async function refreshAfterReading(
  queryClient: QueryClient,
  workspaceId: string,
  documentId?: string,
) {
  await queryClient.invalidateQueries({
    queryKey: ["workspaces", workspaceId, "documents"],
    predicate: (query) => query.queryKey[3] !== documentId || query.queryKey.length > 4,
  });
}

export async function markRead(workspaceId: string, documentId: string) {
  const { error, response } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/documents/{document_id}/read",
    { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
  );
  if (!response.ok) throw new ApiFailure(error);
}

export async function markUnread(workspaceId: string, documentId: string) {
  const { error, response } = await api.DELETE(
    "/api/v1/workspaces/{workspace_id}/documents/{document_id}/read",
    { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
  );
  if (!response.ok) throw new ApiFailure(error);
}

/** "Alles gelezen": at most 200 letters (a list shows no more). */
export async function markManyRead(workspaceId: string, documentIds: string[]) {
  const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/documents/read", {
    params: { path: { workspace_id: workspaceId } },
    body: { document_ids: documentIds.slice(0, 200) },
  });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data.marked;
}
