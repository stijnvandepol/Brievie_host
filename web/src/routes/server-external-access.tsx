import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, redirect } from "@tanstack/react-router";
import { externalAccessQuery } from "../api/queries";
import type { components } from "../api/schema";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Badge, type Intent, Loading, MessageBar } from "../components/ui";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

type ExternalAccess = components["schemas"]["ExternalAccessOut"];
type Check = components["schemas"]["CheckOut"];

const LEVEL_INTENT: Record<string, Intent> = {
  ok: "success",
  warning: "warning",
  problem: "error",
};

function levelLabel(level: string): string {
  if (level === "ok") return t`In orde`;
  if (level === "warning") return t`Let op`;
  return t`Probleem`;
}

/** The sentence of a check code (the server sends codes of a closed list, never text). */
function checkText(code: string): string {
  switch (code) {
    case "hostname_loopback":
      return t`BRIEVIE_HOSTNAME is localhost of 127.0.0.1. Een iPhone kan deze server zo niet bereiken. Zet het IP-adres of de naam van de server in je thuisnetwerk.`;
    case "hostname_mdns_over_vpn":
      return t`BRIEVIE_HOSTNAME eindigt op .local. Zo'n naam werkt niet via een VPN. Gebruik voor een VPN een vast IP-adres, of zet BRIEVIE_VPN_HOSTNAME.`;
    case "public_hostname_missing":
      return t`Er is een tunnel of eigen reverse proxy ingesteld, maar geen BRIEVIE_PUBLIC_HOSTNAME. Zet de publieke naam, zoals brievie.voorbeeld.nl.`;
    case "public_url_overridden_without_public_host":
      return t`BRIEVIE_PUBLIC_URLS is met de hand gezet en bevat het publieke adres niet. Haal BRIEVIE_PUBLIC_URLS en ALLOWED_HOSTS weg, dan volgen ze vanzelf uit de namen.`;
    case "tunnel_not_seen":
      return t`Er is een tunnel-token ingesteld, maar de afgelopen 7 dagen kwam er geen verzoek via de tunnel binnen. Draait de tunnel? Start Brievie opnieuw met ./brievie start; zonder ./brievie zet je COMPOSE_PROFILES=tunnel. Kijk ook of de service in het Cloudflare-dashboard http://caddy:8080 is.`;
    case "upload_limit_above_cloudflare":
      return t`BRIEVIE_MAX_UPLOAD_BYTES is groter dan Cloudflare doorlaat (100 MB). Grote scans komen dan niet door de tunnel.`;
    case "public_web_without_second_factor":
      return t`Inloggen op de webinterface kan van buiten zonder tweede stap. Zet BRIEVIE_PUBLIC_WEB uit, of gebruik een VPN.`;
    case "public_web_second_factor_required":
      return t`Inloggen op de webinterface via het publieke adres vraagt altijd om tweestapsverificatie. Wie dat niet aan heeft, logt alleen thuis of via VPN in.`;
    case "public_web_admin_without_second_factor":
      return t`Niet elke serverbeheerder heeft tweestapsverificatie aan. Wie het niet aan heeft, kan van buiten niet inloggen op de webinterface. Zet het thuis aan onder Account, Beveiliging.`;
    case "public_web_off":
      return t`Via het publieke adres werkt alleen de app. De webinterface gebruik je thuis of via VPN.`;
    case "request_not_secure":
      return t`Jouw verbinding kwam niet via https binnen. Controleer of je reverse proxy https gebruikt en of BRIEVIE_TRUSTED_PROXIES klopt.`;
    case "public_host_not_forwarded":
      return t`Je reverse proxy stuurt de oorspronkelijke naam niet door. Zet in de proxy dat hij de Host-header doorgeeft; anders lukt inloggen niet.`;
    case "trusted_proxy_without_forwarding":
      return t`Jouw verzoek kwam rechtstreeks van een adres uit BRIEVIE_TRUSTED_PROXIES. Staat daar het adres van Docker zelf? Dan kan elk apparaat in je netwerk een ander adres voorwenden. Zet daar alleen het adres van je reverse proxy.`;
    case "cloudflare_reads_along":
      return t`Met Cloudflare Tunnel maakt Cloudflare de versleuteling op zijn eigen servers open. Cloudflare kan alles lezen wat er langskomt, ook gescande brieven. Met een VPN kan niemand onderweg meelezen.`;
    case "app_update_needed":
      return t`Werk de Brievie-app bij op elke gekoppelde iPhone. Oudere versies werken niet via het publieke adres.`;
    case "public_name_from_untrusted_source":
      return t`Er kwamen verzoeken voor de publieke naam binnen van een adres dat niet in BRIEVIE_TRUSTED_PROXIES staat. Brievie behandelde ze als publiek (alleen de app) en telde het directe adres. Is dat je reverse proxy? Zet zijn adres dan in BRIEVIE_TRUSTED_PROXIES.`;
    case "cloudflare_on_https_port":
      return t`Er kwamen verzoeken van Cloudflare binnen op de https-poort. Stel in het Cloudflare-dashboard de service http://caddy:8080 in, niet https://caddy:443. Brievie behandelde ze als publiek.`;
    case "unknown_proxy":
      return t`Er kwamen verzoeken binnen die door een proxy zijn doorgestuurd, van een adres dat niet in BRIEVIE_TRUSTED_PROXIES staat. Brievie behandelde ze als publiek (alleen de app). Is dat je eigen reverse proxy? Zet zijn adres dan in BRIEVIE_TRUSTED_PROXIES.`;
    default:
      return code;
  }
}

function OnOff({ on }: { on: boolean }) {
  return on ? <Badge intent="success">{t`Aan`}</Badge> : <Badge>{t`Uit`}</Badge>;
}

function Way({
  id,
  title,
  on,
  urls,
  children,
}: {
  id: string;
  title: string;
  on: boolean;
  urls: string[];
  children: React.ReactNode;
}) {
  return (
    <section className="panel panel-pad" aria-labelledby={id}>
      <h2 id={id}>
        {title} <OnOff on={on} />
      </h2>
      {children}
      {urls.length > 0 ? (
        <ul>
          {urls.map((url) => (
            <li key={url}>
              <code>{url}</code>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Checks({ checks }: { checks: Check[] }) {
  if (checks.length === 0) {
    return (
      <MessageBar intent="success">{t`Alles wat is ingesteld, werkt zoals bedoeld.`}</MessageBar>
    );
  }
  return (
    <ul className="check-list" aria-label={t`Controles`}>
      {checks.map((check) => (
        <li key={check.code}>
          <Badge intent={LEVEL_INTENT[check.level] ?? "warning"}>{levelLabel(check.level)}</Badge>{" "}
          {checkText(check.code)}
        </li>
      ))}
    </ul>
  );
}

function Page({ found }: { found: ExternalAccess }) {
  const urls = (kind: string) => found.addresses.filter((a) => a.kind === kind).map((a) => a.url);
  const tunnelOn = found.tunnel.token_set;
  const proxyOn = found.trusted_proxies.length > 0;
  // Red only while the web is on outside and its second step is somehow not required.
  const redLine =
    found.public_web && found.checks.some((c) => c.code === "public_web_without_second_factor");
  return (
    <>
      {redLine ? (
        <MessageBar intent="error" role="alert">
          {t`Inloggen op de webinterface kan van buiten zonder tweede stap.`}
        </MessageBar>
      ) : null}
      <section className="panel panel-pad" aria-labelledby="external-checks">
        <h2 id="external-checks">{t`Wat werkt en wat niet`}</h2>
        <Checks checks={found.checks} />
      </section>
      <Way id="way-home" title={t`Thuisnetwerk`} on urls={urls("home")}>
        <p>{t`Altijd aan. Telefoons in je eigen netwerk gebruiken dit adres.`}</p>
      </Way>
      <Way
        id="way-vpn"
        title={t`VPN (aanbevolen)`}
        on={found.vpn_hostname !== ""}
        urls={urls("vpn")}
      >
        <p>{t`Met een VPN (Tailscale, WireGuard) is de verbinding versleuteld van je telefoon tot deze server. Niemand onderweg kan meelezen. Een VPN naar je router werkt zonder instelling hier; draait de VPN op deze server, zet dan BRIEVIE_VPN_HOSTNAME.`}</p>
      </Way>
      <Way
        id="way-tunnel"
        title={t`Cloudflare Tunnel`}
        on={tunnelOn}
        urls={tunnelOn ? urls("public") : []}
      >
        <p>{t`Het makkelijkst, maar Cloudflare kan alles lezen wat er langskomt.`}</p>
        {tunnelOn ? (
          <dl className="facts">
            <dt>{t`Tunnel-token`}</dt>
            <dd>{t`ingesteld (het token zelf staat nooit op dit scherm)`}</dd>
            <dt>{t`Verzoek via de tunnel gezien (laatste 7 dagen)`}</dt>
            <dd>{found.tunnel.connected === "yes" ? t`Ja` : t`Nee`}</dd>
          </dl>
        ) : null}
      </Way>
      <Way
        id="way-proxy"
        title={t`Eigen reverse proxy`}
        on={proxyOn}
        urls={proxyOn ? urls("public") : []}
      >
        {found.untrusted_public_name_peer ? (
          <MessageBar intent="warning">
            {t`Verzoeken via een proxy of voor de publieke naam kwamen van dit adres, dat (nog) niet vertrouwd wordt:`}{" "}
            <code>{found.untrusted_public_name_peer}</code>{" "}
            {t`Zet het alleen in BRIEVIE_TRUSTED_PROXIES als dit je eigen proxy is: elk apparaat in je netwerk kan zo'n verzoek sturen.`}
          </MessageBar>
        ) : null}
        {proxyOn ? (
          <dl className="facts">
            <dt>{t`Vertrouwde adressen`}</dt>
            <dd>{found.trusted_proxies.join(" ")}</dd>
          </dl>
        ) : (
          <p>{t`Uit. Zet BRIEVIE_TRUSTED_PROXIES op het adres van je reverse proxy om hem te gebruiken.`}</p>
        )}
      </Way>
      <section className="panel panel-pad" aria-labelledby="seen-as">
        <h2 id="seen-as">{t`Zo ziet Brievie jouw verbinding`}</h2>
        <dl className="facts">
          <dt>{t`Route`}</dt>
          <dd>{found.seen_as.route === "public" ? t`Publiek adres` : t`Thuisnetwerk of VPN`}</dd>
          <dt>{t`Via https`}</dt>
          <dd>{found.seen_as.secure ? t`Ja` : t`Nee`}</dd>
          <dt>{t`Jouw adres`}</dt>
          <dd>
            <code>{found.seen_as.client_ip || t`onbekend`}</code>
          </dd>
          <dt>{t`Rechtstreeks verbonden met`}</dt>
          <dd>
            <code>{found.seen_as.peer_ip || t`onbekend`}</code>
          </dd>
        </dl>
        <p className="muted">{t`Het adres van je eigen reverse proxy vind je zonder de webinterface open te zetten: stuur één verzoek via de proxy (bijvoorbeeld de app of /api/v1/health) en open deze pagina thuis. Bij Eigen reverse proxy staat dan het adres waar het vandaan kwam. Nooit het adres van Docker zelf.`}</p>
      </section>
      <p>
        <a href={found.docs_url} target="_blank" rel="noopener noreferrer">
          <Icon name="link" />
          {t`Uitleg en voorbeelden op de website`}
        </a>
      </p>
    </>
  );
}

function ExternalAccessPage() {
  usePageTitle(t`Van buiten bereikbaar`);
  const status = useQuery(externalAccessQuery);
  return (
    <Shell>
      <PageHeader
        title={t`Van buiten bereikbaar`}
        description={t`Hoe telefoons en browsers deze server buiten je thuisnetwerk bereiken. Je stelt dit in met deploy/.env of de instellingen van je compose-bestand; deze pagina laat zien wat er nu is ingesteld.`}
      />
      {status.isPending ? <Loading /> : null}
      <ErrorAlert error={status.error} />
      {status.data ? <Page found={status.data} /> : null}
    </Shell>
  );
}

export const serverExternalAccessRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/server/van-buiten",
  beforeLoad: async ({ context }) => {
    const user = await requireUser(context.queryClient);
    // Only a server admin sees this; the server refuses everyone else too.
    if (!user.is_server_admin) throw redirect({ to: "/" });
  },
  component: ExternalAccessPage,
});
