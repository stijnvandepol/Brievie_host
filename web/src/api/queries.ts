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

/** The caller's workspaces. Its own key, not a prefix of the keys of one workspace
 * (`["workspaces", id, …]`): refreshing the list refetches nothing of any workspace. */
export const workspacesQuery = queryOptions({
  queryKey: ["workspace-list"],
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

export type DocumentFilters = {
  inbox_state?: string;
  person_id?: string;
  label_id?: string;
  automatic?: boolean;
  /** "Mijn post": the letters on the caller's own persons (spec S 9). */
  addressed_to_me?: boolean;
  /** With `addressed_to_me`: also the caller's departments ("Ook mijn afdeling"). */
  include_departments?: boolean;
  /** Only the letters the caller has not read. */
  unread_only?: boolean;
};

export const documentsQuery = (
  workspaceId: string,
  filters: DocumentFilters = {},
  options: { poll?: boolean } = {},
) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", filters],
    // While the list itself holds letters (they are being read), fetch it again.
    refetchInterval: options.poll
      ? (query) => ((query.state.data?.length ?? 0) > 0 ? READING_REFETCH_MS : false)
      : false,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/documents", {
        params: {
          path: { workspace_id: workspaceId },
          // Only the filters that are set; an empty value means "no filter".
          query: {
            inbox_state: filters.inbox_state || undefined,
            person_id: filters.person_id || undefined,
            label_id: filters.label_id || undefined,
            automatic: filters.automatic || undefined,
            addressed_to_me: filters.addressed_to_me || undefined,
            include_departments: filters.include_departments || undefined,
            unread_only: filters.unread_only || undefined,
          },
        },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** How often a list or letter is fetched again while a letter is still being read. */
export const READING_REFETCH_MS = 5000;

export const documentQuery = (workspaceId: string, documentId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", documentId],
    refetchInterval: (query) =>
      query.state.data?.status === "processing" ? READING_REFETCH_MS : false,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}",
        { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The letters in the trash the caller sees, the most recently trashed first. */
export const trashQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "trash"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/trash", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The open pairs of letters that are probably one letter scanned twice, newest first. */
export const duplicatesQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "duplicates"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/duplicates", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** One pair with what is the same and what differs. */
export const duplicateQuery = (workspaceId: string, duplicateId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "duplicates", duplicateId],
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/duplicates/{duplicate_id}",
        { params: { path: { workspace_id: workspaceId, duplicate_id: duplicateId } } },
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

/** Mensen: every person with the state of their account (account facts for admins only). */
export const peopleQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "people"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/people", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** Whether a private letter is on the person's name (admins; a yes or no only). */
export const personPrivatePostQuery = (workspaceId: string, personId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "people", personId, "private-post"],
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/v1/workspaces/{workspace_id}/persons/{person_id}/private-post",
        { params: { path: { workspace_id: workspaceId, person_id: personId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    // Asked when a dialog opens; a fresh answer every time.
    staleTime: 0,
    gcTime: 0,
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
    refetchInterval: (query) => ((query.state.data?.new ?? 0) > 0 ? READING_REFETCH_MS : false),
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/inbox/counts", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** How often the menu's numbers are asked again while a page is open. */
export const MENU_REFETCH_MS = 60_000;

/**
 * The menu's numbers in one call (spec S 9): waiting in the Inbox, unread in Mijn post, my
 * open and late tasks, and "Blijft liggen" for sorters. Under "documents", so every change
 * of a letter or a task (which refreshes that key) refreshes the menu too.
 */
export const menuCountsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", "menu-counts"],
    refetchOnWindowFocus: true,
    refetchInterval: MENU_REFETCH_MS,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/menu/counts", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** "Blijft liggen" (spec S 6): only asked for a sorter or admin of an organisation. */
export const lingeringQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", "lingering"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/inbox/lingering", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

export type TaskPart = "mine" | "to_pick_up" | "all";
export type TaskStatus = "open" | "done";

/**
 * "Mijn taken": the caller's tasks of one part. Under the documents key on purpose: every
 * change of a letter (confirm, tick, trash, restore, a name, a merge) already refreshes
 * `["workspaces", id, "documents"]`, and the list is derived from the letters.
 */
export const myTasksQuery = (workspaceId: string, part: TaskPart, status: TaskStatus) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", "my-tasks", part, status],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/my-tasks", {
        params: { path: { workspace_id: workspaceId }, query: { part, status } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The numbers of "Mijn taken" for the navigation: open, overdue, to pick up. */
export const myTaskCountsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "documents", "my-tasks", "counts"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/my-tasks/counts", {
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

export const labelsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "labels"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/labels", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The AI services with a preset, each with the country it is in. */
export const aiSuppliersQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "ai", "suppliers"],
    staleTime: Number.POSITIVE_INFINITY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/ai/suppliers", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** How the workspace's letters are read: `off`, `external` or `local`. */
export const aiStatusQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "ai"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/ai", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The autopilot switches, whether the server allows them, and the notice shown before a yes. */
export const aiAutopilotQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "ai", "autopilot"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/ai/autopilot", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** Example rules to start from; the same for every workspace, so kept for the session. */
export const ruleTemplatesQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "rule-templates"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/rules/templates", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    staleTime: Number.POSITIVE_INFINITY,
  });

/** The workspace's rules in the order they run. */
export const rulesQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "rules"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/rules", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The invitations of a workspace that are still open or just ran out (admins only). The
 * link itself is never part of it: it is shown once, when it is made. */
export const invitationsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "invitations"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/invitations", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** The web sessions of the logged-in user ("Waar je bent ingelogd"). */
export const mySessionsQuery = queryOptions({
  queryKey: ["me", "sessions"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/me/sessions");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

/** The caller's two-step verification ("Beveiliging"). */
export const mySecondFactorQuery = queryOptions({
  queryKey: ["me", "second-factor"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/me/second-factor");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

/** The devices of another account, for an admin who may manage it. */
export const userDevicesQuery = (userId: string) =>
  queryOptions({
    queryKey: ["users", userId, "devices"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/users/{user_id}/devices", {
        params: { path: { user_id: userId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** What the server has to tell the logged-in user, not seen yet (a reset by an admin, a revoked device). */
export const noticesQuery = queryOptions({
  queryKey: ["me", "notices"],
  queryFn: async () => {
    const { data, error } = await api.GET("/api/v1/me/notices");
    if (error !== undefined || !data) throw new ApiFailure(error);
    return data;
  },
});

/** The settings of a workspace that admins can switch. */
export const workspaceSettingsQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "settings"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/settings", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** Who sees what in a workspace: the switches, who sorts post and the sentences (an admin
 * also gets `summary`, the sentences of "Zo is Brievie nu ingesteld"). */
export const visibilityQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "visibility"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/visibility", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

/** How often the page asks again while an export is being built. */
export const EXPORT_POLL_MS = 3000;

/** The caller's own export of their post: none, being built, ready or failed. */
export const myExportQuery = (workspaceId: string) =>
  queryOptions({
    queryKey: ["workspaces", workspaceId, "my-export"],
    refetchInterval: (query) => (query.state.data?.status === "pending" ? EXPORT_POLL_MS : false),
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/workspaces/{workspace_id}/my-export", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });

const SEARCH_PAGE_SIZE = 30;

export type DocumentSearch = {
  q: string;
  document_type?: string;
  label_id?: string;
  person_id?: string;
  inbox_state?: string;
  date_from?: string;
  date_to?: string;
  offset?: number;
};

/**
 * A search in the letters of a workspace. A POST, so the term travels in the body. It is
 * deliberately no query: a query key would hold the term in the cache and its devtools.
 */
export async function searchDocuments(workspaceId: string, search: DocumentSearch) {
  const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/documents/search", {
    params: { path: { workspace_id: workspaceId } },
    body: {
      q: search.q,
      document_type: search.document_type ?? "",
      inbox_state: search.inbox_state ?? "",
      label_id: search.label_id || null,
      person_id: search.person_id || null,
      date_from: search.date_from || null,
      date_to: search.date_to || null,
      offset: search.offset ?? 0,
      limit: SEARCH_PAGE_SIZE,
    },
  });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}
