import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { meQuery, visibilityQuery } from "../api/queries";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading } from "../components/ui";
import { workspaceRoute } from "./workspace";

/** What to do next, in the words of S 4.2, after "Back-up instellen" (done on the server). */
function BackupStep() {
  return (
    <li>
      {t`Back-up instellen: typ op de server`} <code>./brievie backup --init</code>
    </li>
  );
}

/** The recovery sheet: a server admin prints it from the web interface (no terminal). */
function RecoverySheetStep() {
  const { data: me } = useQuery(meQuery);
  if (!me?.is_server_admin) return null;
  return (
    <li>
      <Link to="/server/herstelblad">{t`Herstelblad downloaden en bewaren`}</Link>
    </li>
  );
}

/**
 * "Zo is Brievie nu ingesteld": right after the installation (and whenever an admin comes
 * back), the sentences of who sees what, then the next steps for Thuis or Organisatie.
 */
function SetupSummaryPage() {
  usePageTitle(t`Zo is Brievie nu ingesteld`);
  const { workspace } = setupSummaryRoute.useRouteContext();
  const visibility = useQuery(visibilityQuery(workspace.id));
  const params = { workspaceId: workspace.id };
  const data = visibility.data;
  const sentences = data
    ? data.summary && data.summary.length > 0
      ? data.summary
      : data.sentences
    : [];
  const business = (data?.kind ?? workspace.kind) === "business";
  const isAdmin = workspace.role === "admin";
  return (
    <Shell workspace={workspace}>
      <PageHeader title={t`Zo is Brievie nu ingesteld`} />
      {visibility.isPending ? <Loading /> : null}
      <ErrorAlert error={visibility.error} />
      {data ? (
        <section className="panel panel-pad" aria-labelledby="summary-now">
          <h2 id="summary-now">{workspace.name}</h2>
          <ul className="sentence-list">
            {sentences.map((sentence) => (
              <li key={sentence}>{sentence}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {data && isAdmin ? (
        <nav className="panel panel-pad" aria-labelledby="summary-next">
          <h2 id="summary-next">{t`Wat nu?`}</h2>
          <ul className="sentence-list">
            {business ? (
              <>
                <li>
                  <Link to="/w/$workspaceId/afdelingen" params={params}>
                    {t`Afdelingen maken`}
                  </Link>
                </li>
                <li>
                  <Link to="/w/$workspaceId/mensen" params={params}>
                    {t`Mensen uitnodigen`}
                  </Link>
                </li>
                <li>
                  <Link to="/w/$workspaceId/instellingen/wie-ziet-wat" params={params}>
                    {t`Iemand post laten verdelen`}
                  </Link>
                </li>
                <BackupStep />
                <RecoverySheetStep />
              </>
            ) : (
              <>
                <li>
                  <Link to="/w/$workspaceId/mensen" params={params}>
                    {t`Iemand uitnodigen`}
                  </Link>
                </li>
                <li>
                  <Link to="/w/$workspaceId/mensen" params={params}>
                    {t`Mensen toevoegen`}
                  </Link>
                </li>
                <BackupStep />
                <RecoverySheetStep />
                <li>
                  <Link to="/w/$workspaceId/instellingen/wie-ziet-wat" params={params}>
                    {t`Wie ziet wat wijzigen`}
                  </Link>
                </li>
              </>
            )}
          </ul>
        </nav>
      ) : null}
    </Shell>
  );
}

export const setupSummaryRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "ingesteld",
  component: SetupSummaryPage,
});
