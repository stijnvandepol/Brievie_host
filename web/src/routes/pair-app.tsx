import { plural, t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useRef, useState } from "react";
import { Button, Label, TextArea, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { formatTimeLeft, useSecondsLeft } from "../components/countdown";
import { DeviceTable } from "../components/device-table";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { MessageBar } from "../components/ui";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

/** The server addresses and certificate pin the app is given; null when the payload is not usable. */
function readPayload(qrPayload: string): { urls: string[]; hasPin: boolean } | null {
  try {
    const parsed: unknown = JSON.parse(qrPayload);
    if (typeof parsed !== "object" || parsed === null) return null;
    const urls = "urls" in parsed && Array.isArray(parsed.urls) ? parsed.urls : [];
    return {
      urls: urls.filter((url): url is string => typeof url === "string"),
      hasPin: "ca_sha256" in parsed && typeof parsed.ca_sha256 === "string",
    };
  } catch {
    return null;
  }
}

const clock = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit" });

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** True when every address in the payload points at the server's own machine. */
function onlyReachableOnTheServer(qrPayload: string): boolean {
  try {
    const parsed: unknown = JSON.parse(qrPayload);
    if (typeof parsed !== "object" || parsed === null || !("urls" in parsed)) return false;
    const urls = parsed.urls;
    if (!Array.isArray(urls) || urls.length === 0) return false;
    return urls.every(
      (url) => typeof url === "string" && LOCAL_HOSTS.has(new URL(url).hostname.toLowerCase()),
    );
  } catch {
    return false;
  }
}

type CopyState = "idle" | "copied" | "failed";

function PairAppPage() {
  usePageTitle(t`Apparaten`);
  const navigate = useNavigate();
  const result = useRef<HTMLElement>(null);
  const newCode = useRef<HTMLButtonElement>(null);
  const [copy, setCopy] = useState<CopyState>("idle");
  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/devices/pairing-codes");
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: () => setCopy("idle"),
    onError: async (error) => {
      // The session ended while this page was open.
      if (error instanceof ApiFailure && error.code === "not_authenticated") {
        await navigate({ to: "/login" });
      }
    },
  });
  const pairing = create.data;
  const secondsLeft = useSecondsLeft(pairing?.expires_at);
  const expired = pairing !== undefined && secondsLeft === 0;
  const details = pairing ? readPayload(pairing.qr_payload) : null;

  // A new code appears below the button: bring keyboard and screen reader users to it.
  useEffect(() => {
    if (pairing) result.current?.focus();
  }, [pairing]);

  // The code ran out: the button for a new one is where focus belongs.
  useEffect(() => {
    if (expired) newCode.current?.focus();
  }, [expired]);

  async function copyPayload(payload: string) {
    try {
      await navigator.clipboard.writeText(payload);
      setCopy("copied");
    } catch {
      // No Clipboard API, or the browser said no: the text is shown to copy by hand.
      setCopy("failed");
    }
  }

  let status = "";
  if (expired) status = t`Deze code is verlopen.`;
  else if (copy === "copied") status = t`Gekopieerd`;
  else if (copy === "failed") status = t`Kopiëren lukt niet. Kopieer de tekst hieronder zelf.`;

  return (
    <Shell>
      <PageHeader
        title={t`Apparaten`}
        description={t`Koppel de Brievie-app op je telefoon aan deze server en zie welke telefoons gekoppeld zijn. Met de app scan je brieven.`}
        commands={
          pairing === undefined || expired ? (
            <>
              {pairing === undefined ? (
                <Button
                  className="button"
                  onPress={() => create.mutate()}
                  isPending={create.isPending}
                >
                  <Icon name="link" />
                  {t`Koppelcode maken`}
                </Button>
              ) : null}
              {expired ? (
                <Button
                  ref={newCode}
                  className="button"
                  onPress={() => create.mutate()}
                  isPending={create.isPending}
                >
                  <Icon name="refresh" />
                  {t`Nieuwe code`}
                </Button>
              ) : null}
            </>
          ) : null
        }
      />

      <ErrorAlert error={create.error} />

      {pairing !== undefined && onlyReachableOnTheServer(pairing.qr_payload) ? (
        <MessageBar intent="warning" role="alert">
          {t`Je server staat ingesteld op 'localhost'. Je telefoon kan die niet bereiken. Stel in het bestand .env de naam of het IP-adres van je server in (BRIEVIE_HOSTNAME).`}
        </MessageBar>
      ) : null}

      <p role="status" aria-live="polite" className="status">
        {status}
      </p>

      {pairing !== undefined && !expired ? (
        <section
          className="panel pairing"
          ref={result}
          tabIndex={-1}
          aria-labelledby="pairing-title"
        >
          <div className="pairing-details">
            <h2 id="pairing-title">{t`Koppelcode`}</h2>
            <dl className="facts">
              <dt>{t`Code`}</dt>
              <dd className="num code">{pairing.code}</dd>
              <dt>{t`Geldig tot`}</dt>
              <dd className="num">
                <time dateTime={pairing.expires_at}>
                  {clock.format(new Date(pairing.expires_at))}
                </time>{" "}
                <span role="timer">{t`(nog ${formatTimeLeft(secondsLeft)})`}</span>
              </dd>
              {details && details.urls.length > 0 ? (
                <>
                  <dt>
                    {plural(details.urls.length, { one: "Serveradres", other: "Serveradressen" })}
                  </dt>
                  <dd className="wrap">
                    {details.urls.map((url) => (
                      <span key={url} className="block">
                        {url}
                      </span>
                    ))}
                  </dd>
                </>
              ) : null}
              {details ? (
                <>
                  <dt>{t`Certificaat`}</dt>
                  <dd>
                    {details.hasPin ? t`Eigen CA, vingerafdruk zit in de code` : t`Geen eigen CA`}
                  </dd>
                </>
              ) : null}
            </dl>
            <p className="muted">{t`Open de app op je telefoon en scan de code. De telefoon moet op hetzelfde netwerk zitten als deze server.`}</p>
            <div>
              <Button className="button secondary" onPress={() => copyPayload(pairing.qr_payload)}>
                <Icon name="copy" />
                {t`Kopieer koppelgegevens`}
              </Button>
            </div>
            {copy === "failed" ? (
              <TextField isReadOnly className="field payload">
                <Label>{t`Koppelgegevens`}</Label>
                <TextArea
                  value={pairing.qr_payload}
                  rows={5}
                  onFocus={(event) => event.currentTarget.select()}
                />
              </TextField>
            ) : null}
          </div>
          <div className="pairing-qr">
            <QRCodeSVG
              value={pairing.qr_payload}
              title={t`QR-code om de app te koppelen`}
              size={200}
              marginSize={4}
              level="M"
            />
            <span className="muted small">{t`Scan met de Brievie-app`}</span>
          </div>
        </section>
      ) : null}

      <DeviceTable />
    </Shell>
  );
}

export const pairAppRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/koppel-app",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: PairAppPage,
});
