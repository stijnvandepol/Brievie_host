import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Switch } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery, workspaceSettingsQuery } from "../api/queries";
import { ErrorAlert } from "./page";

/**
 * "Privé" on Instellingen: "Zorg-post is privé" (the workspace's admins), and emergency
 * access, which only the server admin switches; everyone else reads its state.
 */
export function PrivacySettings({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const settings = useQuery(workspaceSettingsQuery(workspaceId));
  const { data: me } = useQuery(meQuery);
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: workspaceSettingsQuery(workspaceId).queryKey });
  const care = useMutation({
    mutationFn: async (on: boolean) => {
      const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/settings", {
        params: { path: { workspace_id: workspaceId } },
        body: { care_is_private: on },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSettled: refresh,
  });
  const emergency = useMutation({
    mutationFn: async (on: boolean) => {
      const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/emergency-access", {
        params: { path: { workspace_id: workspaceId } },
        body: { enabled: on },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSettled: refresh,
  });
  if (!settings.data) return null;
  const emergencyOn = settings.data.emergency_access ?? false;
  return (
    <section className="panel panel-pad" id="prive" aria-labelledby="settings-private">
      <h2 id="settings-private">{t`Privé`}</h2>
      <Switch
        className="switch"
        isSelected={settings.data.care_is_private ?? false}
        isDisabled={care.isPending}
        onChange={(on) => care.mutate(on)}
      >
        <span className="switch-track" aria-hidden="true" />
        {t`Zorg-post is privé`}
      </Switch>
      <p className="switch-hint">{t`Een brief met het label Zorg wordt dan vanzelf privé. Brieven die er al zijn, blijven zoals ze zijn.`}</p>
      {me?.is_server_admin ? (
        <>
          <Switch
            className="switch"
            isSelected={emergencyOn}
            isDisabled={emergency.isPending}
            onChange={(on) => emergency.mutate(on)}
          >
            <span className="switch-track" aria-hidden="true" />
            {t`Noodtoegang voor de beheerder`}
          </Switch>
          <p className="switch-hint">{t`Een beheerder kan dan een uur lang post lezen die hij anders niet ziet, met een reden die in het logboek komt. Uitzetten beëindigt een noodtoegang die nog loopt.`}</p>
        </>
      ) : (
        <p>
          {emergencyOn
            ? t`Noodtoegang voor de beheerder staat aan. Alleen de serverbeheerder kan dit veranderen.`
            : t`Noodtoegang voor de beheerder staat uit. Alleen de serverbeheerder kan dit veranderen.`}
        </p>
      )}
      <ErrorAlert error={care.error ?? emergency.error} />
    </section>
  );
}
