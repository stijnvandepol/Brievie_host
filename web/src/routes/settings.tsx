import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { Label, Radio, RadioGroup, Switch } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { workspaceSettingsQuery } from "../api/queries";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { PrivacySettings } from "../components/privacy-settings";
import { Loading } from "../components/ui";
import { isLocale, LANGUAGE_NAMES, LOCALES } from "../i18n";
import { workspaceRoute } from "./workspace";

/** A link to a page that lives under Instellingen (spec S 4.2): what it is and the link. */
function Entry({
  id,
  title,
  text,
  children,
}: {
  id: string;
  title: string;
  text: string;
  children: ReactNode;
}) {
  return (
    <section className="panel panel-pad" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      <p className="muted">{text}</p>
      <div className="settings-links">{children}</div>
    </section>
  );
}

/**
 * "Instellingen", at the bottom of the menu, for everyone (spec S 4.2, decision B6): your
 * account, your devices, who can see your post and the AI; an admin also gets the
 * workspace's own settings and the log. The old addresses of those pages keep working.
 */
function SettingsPage() {
  usePageTitle(t`Instellingen`);
  const { workspace } = settingsRoute.useRouteContext();
  const admin = workspace.role === "admin";
  const params = { workspaceId: workspace.id };
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Instellingen`}
        description={
          admin
            ? t`Je account, je apparaten en wat leden in ${workspace.name} mogen doen.`
            : t`Je account, je apparaten en wie je post kan zien.`
        }
      />
      <Entry
        id="settings-account"
        title={t`Jouw account`}
        text={t`Waar je bent ingelogd, je wachtwoord en de taal van Brievie.`}
      >
        <Link to="/account/sessies" className="button secondary">
          {t`Waar je bent ingelogd`}
        </Link>
        <Link to="/account/wachtwoord" className="button secondary">
          {t`Wachtwoord wijzigen`}
        </Link>
        <Link to="/account/taal" className="button secondary">
          {t`Taal`}
        </Link>
      </Entry>
      <Entry
        id="settings-devices"
        title={t`Apparaten`}
        text={t`De telefoons waarmee je scant, en een nieuwe koppelen.`}
      >
        <Link to="/koppel-app" className="button secondary">
          {t`Apparaten`}
        </Link>
      </Entry>
      <Entry
        id="settings-post-access"
        title={t`Wie kan mijn post zien?`}
        text={t`Wie jouw brieven kan lezen in ${workspace.name}, en waarom.`}
      >
        <Link to="/w/$workspaceId/wie-ziet-mijn-post" params={params} className="button secondary">
          {t`Wie kan mijn post zien?`}
        </Link>
      </Entry>
      <Entry
        id="settings-ai"
        title={t`AI`}
        text={t`Of AI je brieven leest, en welke dienst dat doet.`}
      >
        <Link to="/w/$workspaceId/ai" params={params} className="button secondary">
          {t`AI`}
        </Link>
      </Entry>
      {admin ? <WorkspaceSettings workspace={workspace} /> : null}
    </Shell>
  );
}

/** The workspace's own settings: admins only (the server refuses everyone else too). */
function WorkspaceSettings({ workspace }: { workspace: { id: string; name: string } }) {
  const queryClient = useQueryClient();
  const settings = useQuery(workspaceSettingsQuery(workspace.id));
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: async (body: { allow_member_export?: boolean; summary_language?: string }) => {
      const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/settings", {
        params: { path: { workspace_id: workspace.id } },
        body,
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
    <>
      <h2 className="settings-heading">{t`Werkruimte ${workspace.name}`}</h2>
      {settings.isPending ? <Loading /> : null}
      <ErrorAlert error={settings.error ?? save.error} />
      <section className="panel panel-pad" aria-labelledby="settings-visibility">
        <h2 id="settings-visibility">{t`Wie ziet wat`}</h2>
        <p className="muted">{t`Wie welke post ziet: iedereen alles, of ieder zijn eigen post en die van zijn afdelingen.`}</p>
        <div>
          <Link
            to="/w/$workspaceId/instellingen/wie-ziet-wat"
            params={{ workspaceId: workspace.id }}
            className="button secondary"
          >
            {t`Wie ziet wat`}
          </Link>
        </div>
      </section>
      <PrivacySettings workspaceId={workspace.id} />
      {settings.data ? (
        <section className="panel panel-pad" aria-labelledby="settings-members">
          <h2 id="settings-members">{t`Leden`}</h2>
          <Switch
            className="switch"
            isSelected={settings.data.allow_member_export}
            isDisabled={save.isPending}
            onChange={(on) => {
              setSaved(false);
              save.mutate({ allow_member_export: on });
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
      {settings.data ? (
        <section className="panel panel-pad" aria-labelledby="settings-summary">
          <h2 id="settings-summary">{t`Samenvattingen`}</h2>
          <RadioGroup
            className="choice form-narrow"
            value={settings.data.summary_language ?? "nl"}
            isDisabled={save.isPending}
            onChange={(next) => {
              if (!isLocale(next)) return;
              setSaved(false);
              save.mutate({ summary_language: next });
            }}
          >
            <Label className="choice-label">{t`Taal van de samenvatting en de taken die AI maakt`}</Label>
            <div className="cards cards-stack">
              {LOCALES.map((locale) => (
                <Radio
                  key={locale}
                  value={locale}
                  className="card"
                  aria-label={LANGUAGE_NAMES[locale]}
                >
                  <span className="card-top" aria-hidden="true">
                    <strong lang={locale}>{LANGUAGE_NAMES[locale]}</strong>
                    <span className="radio-indicator" />
                  </span>
                </Radio>
              ))}
            </div>
          </RadioGroup>
          <p className="switch-hint">{t`Geldt voor brieven die AI vanaf nu leest. De brief zelf blijft zoals hij is.`}</p>
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
    </>
  );
}

export const settingsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "instellingen",
  component: SettingsPage,
});
