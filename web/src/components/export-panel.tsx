import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { myExportQuery } from "../api/queries";
import { dateFormat } from "../format";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";
import { Loading } from "./ui";

const dateTime = dateFormat({ dateStyle: "medium", timeStyle: "short" });

/**
 * "Mijn archief exporteren": make a ZIP of one's own post and documents (a PDF each and the
 * data), wait while it is built, then download it (it stays 24 hours). Hidden behind a
 * sentence when the workspace does not allow it. With `offerAll` an admin also gets "Alles
 * exporteren": everything they may read, whatever that switch says (archive spec S 9,
 * AR13). One export at a time per member. The download is a plain link: the browser sends
 * the session cookie.
 */
export function ExportPanel({
  workspaceId,
  isAdmin,
  offerAll = false,
}: {
  workspaceId: string;
  isAdmin: boolean;
  offerAll?: boolean;
}) {
  const queryClient = useQueryClient();
  const state = useQuery(myExportQuery(workspaceId));
  const start = useMutation({
    mutationFn: async (scope: "mine" | "all") => {
      const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/my-export", {
        params: { path: { workspace_id: workspaceId }, query: { scope } },
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
  const all = offerAll && isAdmin;
  const offAdmin = (
    <p className="muted">
      {t`Exporteren staat uit voor de leden van deze werkruimte.`}{" "}
      <Link to="/w/$workspaceId/instellingen" params={{ workspaceId }}>
        {t`Zet het aan bij Instellingen.`}
      </Link>
    </p>
  );
  if (!current.allowed && !all) {
    return isAdmin ? (
      offAdmin
    ) : (
      <p className="muted">
        {t`Exporteren staat uit voor deze werkruimte. Vraag een beheerder om dit aan te zetten.`}
      </p>
    );
  }
  const letters = plural(current.document_count ?? 0, {
    one: "# document",
    other: "# documenten",
  });
  const everything = current.scope === "all";
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
          {everything
            ? t`Download alles (${letters}, geldig tot ${dateTime.format(new Date(current.expires_at))})`
            : t`Download (${letters}, geldig tot ${dateTime.format(new Date(current.expires_at))})`}
        </a>
      ) : null}
      {current.status === "failed" ? (
        <p>{t`De export is niet gelukt. Probeer het opnieuw.`}</p>
      ) : null}
      <ErrorAlert error={start.error} />
      {current.status === "pending" ? null : (
        <div className="panel-actions">
          {current.allowed ? (
            <Button
              className="button secondary"
              isPending={start.isPending && start.variables === "mine"}
              isDisabled={start.isPending}
              onPress={() => start.mutate("mine")}
            >
              {t`Mijn archief exporteren`}
            </Button>
          ) : null}
          {all ? (
            <Button
              className="button secondary"
              isPending={start.isPending && start.variables === "all"}
              isDisabled={start.isPending}
              onPress={() => start.mutate("all")}
            >
              {t`Alles exporteren`}
            </Button>
          ) : null}
        </div>
      )}
      {all ? (
        <p className="muted small">
          {t`Alles exporteren: alles wat jij mag lezen. Privébrieven die jij niet mag lezen, zitten er niet in. Het is geen back-up.`}
        </p>
      ) : null}
      {all && !current.allowed ? offAdmin : null}
    </div>
  );
}
