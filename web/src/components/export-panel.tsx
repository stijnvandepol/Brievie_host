import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { myExportQuery } from "../api/queries";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";
import { Loading } from "./ui";

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium", timeStyle: "short" });

/**
 * "Mijn post exporteren": make a ZIP of one's own post, wait while it is built, then
 * download it (it stays 24 hours). Hidden behind a sentence when the workspace does not
 * allow it. The download is a plain link: the browser sends the session cookie.
 */
export function ExportPanel({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const state = useQuery(myExportQuery(workspaceId));
  const start = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/my-export", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (data) => queryClient.setQueryData(myExportQuery(workspaceId).queryKey, data),
  });

  if (state.isPending) return <Loading />;
  if (state.error) return <ErrorAlert error={state.error} />;
  const current = state.data;
  if (!current) return null;
  if (!current.allowed) {
    return <p className="muted">{t`Exporteren staat uit voor deze werkruimte.`}</p>;
  }
  const letters = plural(current.document_count ?? 0, { one: "# brief", other: "# brieven" });
  return (
    <div className="export-panel">
      {current.status === "pending" ? (
        <p role="status" className="status">
          <span className="spinner" aria-hidden="true" />
          {t`Wordt gemaakt…`}
        </p>
      ) : null}
      {current.status === "ready" && current.expires_at ? (
        <a className="button" href={`/api/v1/workspaces/${workspaceId}/my-export/file`}>
          <Icon name="arrowDown" />
          {t`Download (${letters}, geldig tot ${dateTime.format(new Date(current.expires_at))})`}
        </a>
      ) : null}
      {current.status === "failed" ? (
        <p>{t`De export is niet gelukt. Probeer het opnieuw.`}</p>
      ) : null}
      <ErrorAlert error={start.error} />
      {current.status === "pending" ? null : (
        <div>
          <Button
            className={current.status === "ready" ? "button secondary" : "button"}
            isPending={start.isPending}
            onPress={() => start.mutate()}
          >
            {current.status === "ready" ? t`Opnieuw exporteren` : t`Mijn post exporteren`}
          </Button>
        </div>
      )}
    </div>
  );
}
