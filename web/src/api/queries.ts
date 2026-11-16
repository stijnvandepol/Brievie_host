import { queryOptions } from "@tanstack/react-query";
import { ApiFailure, api } from "./client";

export const setupStatusQuery = queryOptions({
  queryKey: ["setup-status"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/setup");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

/** The logged-in user, or `null` when nobody is logged in. */
export const meQuery = queryOptions({
  queryKey: ["me"],
  queryFn: async () => {
    const { data, error, response } = await api.GET("/api/v1/me");
    if (response.status === 401) return null;
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

export const workspacesQuery = queryOptions({
  queryKey: ["workspaces"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/workspaces");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

export const devicesQuery = queryOptions({
  queryKey: ["devices"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/devices");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

export type DocumentFilters = { inbox_state?: string; person_id?: string; label_id?: string };

export const documentsQuery = (workspaceId: string, filters: DocumentFilters = {}) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", filters],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/documents", {
        params: {
          path: { workspace_id: workspaceId },
          // Only the filters that are set; an empty value means "no filter".
          query: {
            inbox_state: filters.inbox_state || undefined,
            person_id: filters.person_id || undefined,
            label_id: filters.label_id || undefined,
          },
        },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

export const documentQuery = (workspaceId: string, documentId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", documentId],
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}",
        { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

export const personsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "persons"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/persons", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

export const membersQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "members"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/members", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

export const inboxCountsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "inbox-counts"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/inbox/counts", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

export const documentEventsQuery = (workspaceId: string, documentId: string) =>
  queryOptions({
    // Under the document's key, so a change of the document refreshes its history too.
    queryKey: ["workspaces", workspaceId, "documents", documentId, "events"],
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/events",
        { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
