import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, redirect } from "@tanstack/react-router";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { backupStatusQuery } from "../api/queries";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading, MessageBar } from "../components/ui";
import { dateFormat } from "../format";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

const moment = dateFormat({ dateStyle: "medium", timeStyle: "short" });

/** A size the way a person reads it: "12,4 MB". */
function size(bytes: number): string {
  const units = ["B", "kB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unit]}`;
}

function NotSetUp({ contradicting }: { contradicting: boolean }) {
  return (
    <section className="panel panel-pad" aria-labelledby="backup-howto">
      <h2 id="backup-howto">{t`Zo stel je een back-up in`}</h2>
      {contradicting ? (
        <p>{t`De instellingen voor de back-up spreken elkaar tegen: je kunt een map óf een adres instellen, niet allebei.`}</p>
      ) : (
        <p>{t`Er is nog geen back-up ingesteld. Zonder back-up zijn je documenten weg als de schijf van deze server kapotgaat.`}</p>
      )}
      <p>{t`Zet deze regels bij de instellingen van Brievie (in het compose-bestand, Portainer of Synology) en start Brievie opnieuw:`}</p>
      <pre className="code-block">
        {"BRIEVIE_BACKUP_DIR=/volume1/brievie-backup\nBRIEVIE_BACKUP_AUTO_INIT=true"}
      </pre>
      <p className="muted">{t`De map moet op een andere schijf staan dan de gegevens en eigendom zijn van gebruiker 10001. Wil je een back-up op afstand? Zet dan BRIEVIE_BACKUP_REPOSITORY in plaats van de map.`}</p>
    </section>
  );
}

function BackupPage() {
  usePageTitle(t`Back-up`);
  const queryClient = useQueryClient();
  const status = useQuery(backupStatusQuery);
  const start = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/server/backup-runs");
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: backupStatusQuery.queryKey }),
  });
  const found = status.data;
  const configured =
    found !== undefined && (found.destination === "folder" || found.destination === "remote");
  return (
    <Shell>
      <PageHeader
        title={t`Back-up`}
        description={t`Hoe het staat met de back-up van deze server. Elke nacht maakt Brievie vanzelf een back-up zodra er een bestemming is ingesteld.`}
        commands={
          configured ? (
            <Button
              className="button"
              onPress={() => start.mutate()}
              isPending={start.isPending}
              isDisabled={found.running}
            >
              <Icon name="save" />
              {t`Nu back-uppen`}
            </Button>
          ) : null
        }
      />
      {status.isPending ? <Loading /> : null}
      <ErrorAlert error={status.error ?? start.error} />
      {found && !configured ? <NotSetUp contradicting={found.destination === "invalid"} /> : null}
      {found && configured ? (
        <>
          <p role="status" aria-live="polite" className="status">
            {found.running ? t`Er loopt een back-up. Dit scherm ververst zichzelf.` : ""}
          </p>
          {found.late ? (
            <MessageBar intent="warning" role="alert">
              {found.last_success_at
                ? t`De laatste gelukte back-up is meer dan anderhalve dag oud. Kijk hieronder wat er misging.`
                : t`Er is nog geen enkele back-up gelukt.`}
            </MessageBar>
          ) : null}
          {found.last_failure_reason ? (
            <MessageBar intent="error" role="alert">
              {found.last_failure_reason}
            </MessageBar>
          ) : null}
          <section className="panel panel-pad" aria-labelledby="backup-facts">
            <h2 id="backup-facts">{t`Laatste back-up`}</h2>
            <dl className="facts">
              <dt>{t`Bestemming`}</dt>
              <dd>{found.destination === "folder" ? t`Een map` : t`Een plek op afstand`}</dd>
              <dt>{t`Laatste gelukte back-up`}</dt>
              <dd>
                {found.last_success_at ? (
                  <time dateTime={found.last_success_at}>
                    {moment.format(new Date(found.last_success_at))}
                  </time>
                ) : (
                  t`nog geen`
                )}
              </dd>
              {typeof found.last_success_size_bytes === "number" ? (
                <>
                  <dt>{t`Grootte`}</dt>
                  <dd>{size(found.last_success_size_bytes)}</dd>
                </>
              ) : null}
              <dt>{t`Laatste controle`}</dt>
              <dd>
                {found.last_check_at ? (
                  <time dateTime={found.last_check_at}>
                    {moment.format(new Date(found.last_check_at))}
                  </time>
                ) : (
                  t`nog geen`
                )}
              </dd>
            </dl>
          </section>
        </>
      ) : null}
    </Shell>
  );
}

export const backupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/server/back-up",
  beforeLoad: async ({ context }) => {
    const user = await requireUser(context.queryClient);
    // Only a server admin sees the server's back-up; the server refuses everyone else too.
    if (!user.is_server_admin) throw redirect({ to: "/" });
  },
  component: BackupPage,
});
