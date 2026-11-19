import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, redirect } from "@tanstack/react-router";
import { useState } from "react";
import { Switch } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { workspaceSettingsQuery } from "../api/queries";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading } from "../components/ui";
import { workspaceRoute } from "./workspace";

function SettingsPage() {
  usePageTitle(t`Instellingen`);
  const { workspace } = settingsRoute.useRouteContext();
  const queryClient = useQueryClient();
  const settings = useQuery(workspaceSettingsQuery(workspace.id));
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: async (allow: boolean) => {
      const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/settings", {
        params: { path: { workspace_id: workspace.id } },
        body: { allow_member_export: allow },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(workspaceSettingsQuery(workspace.id).queryKey, data);
      setSaved(true);
    },
    onError: () => setSaved(false),
  });

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Instellingen`}
        description={t`Wat leden in ${workspace.name} mogen doen, en wat er is gebeurd.`}
      />
      {settings.isPending ? <Loading /> : null}
      <ErrorAlert error={settings.error ?? save.error} />
      {settings.data ? (
        <section className="panel panel-pad" aria-labelledby="settings-members">
          <h2 id="settings-members">{t`Leden`}</h2>
          <Switch
            className="switch"
            isSelected={settings.data.allow_member_export}
            isDisabled={save.isPending}
            onChange={(on) => {
              setSaved(false);
              save.mutate(on);
            }}
          >
            <span className="switch-track" aria-hidden="true" />
            {t`Leden mogen hun eigen post exporteren`}
          </Switch>
          <p className="switch-hint">{t`Elk lid kan dan een ZIP-bestand met de eigen post downloaden.`}</p>
          <p role="status" aria-live="polite" className="status">
            {saved ? t`Opgeslagen` : ""}
          </p>
        </section>
      ) : null}
      <section className="panel panel-pad" aria-labelledby="settings-log">
        <h2 id="settings-log">{t`Logboek`}</h2>
        <p className="muted">{t`Wie heeft wie uitgenodigd, welke rol veranderde en wat er verder is ingesteld.`}</p>
        <div>
          <Link
            to="/w/$workspaceId/instellingen/logboek"
            params={{ workspaceId: workspace.id }}
            className="button secondary"
          >
            {t`Logboek`}
          </Link>
        </div>
      </section>
    </Shell>
  );
}

export const settingsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "instellingen",
  // Settings are for admins; the server refuses everyone else too.
  beforeLoad: ({ context }) => {
    if (context.workspace.role !== "admin") {
      throw redirect({
        to: "/w/$workspaceId/inbox",
        params: { workspaceId: context.workspace.id },
      });
    }
  },
  component: SettingsPage,
});
