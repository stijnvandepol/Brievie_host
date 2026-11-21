import type { QueryClient } from "@tanstack/react-query";
import { ApiFailure, api } from "./client";

/**
 * After a letter went to the trash, came back or was purged: everything that counts or lists
 * letters is read again. Tasks live in the letters' answers; search results are not cached
 * (a search runs again when its page opens).
 */
export async function refreshAfterTrash(queryClient: QueryClient, workspaceId: string) {
  await Promise.all(
    ["documents", "inbox-counts", "trash", "persons", "labels"].map((key) =>
      queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, key] }),
    ),
  );
}

export async function trashDocument(workspaceId: string, documentId: string) {
  const { data, error } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/documents/{document_id}/trash",
    { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
  );
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

/** Puts a letter back; the answer is the letter as it is now. */
export async function restoreDocument(workspaceId: string, documentId: string) {
  const { data, error } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/documents/{document_id}/restore",
    { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
  );
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

export async function purgeDocument(workspaceId: string, documentId: string) {
  const { error, response } = await api.DELETE(
    "/api/v1/workspaces/{workspace_id}/trash/{document_id}",
    { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
  );
  if (!response.ok) throw new ApiFailure(error);
}

/** Empties the trash up to `upTo`, the newest `trashed_at` the admin was shown. */
export async function emptyTrash(workspaceId: string, upTo: string | null) {
  const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/trash/empty", {
    params: { path: { workspace_id: workspaceId } },
    body: { up_to: upTo },
  });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}
