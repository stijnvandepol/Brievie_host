import { t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useRef, useState } from "react";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

/** Seconds left until `expiresAt`, counting down; 0 means expired. */
function useSecondsLeft(expiresAt: string | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [expiresAt]);
  return expiresAt ? Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 1000)) : 0;
}

function formatClock(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function PairAppPage() {
  usePageTitle(t`App koppelen`);
  const navigate = useNavigate();
  const result = useRef<HTMLDivElement>(null);
  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/devices/pairing-codes");
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
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

  // A new code appears below the button: bring keyboard and screen reader users to it.
  useEffect(() => {
    if (pairing) result.current?.focus();
  }, [pairing]);

  return (
    <Shell>
      <h1>{t`App koppelen`}</h1>
      <p>{t`Koppel de Brievie-app op je telefoon aan deze server. Maak een code en scan de QR-code met de app. Lukt scannen niet? Typ dan de code in.`}</p>

      {pairing === undefined ? (
        <Button className="button" onPress={() => create.mutate()} isPending={create.isPending}>
          {t`Koppelcode maken`}
        </Button>
      ) : null}

      <ErrorAlert error={create.error} />

      {pairing !== undefined && !expired ? (
        <div className="pairing" ref={result} tabIndex={-1}>
          <QRCodeSVG
            value={pairing.qr_payload}
            title={t`QR-code om de app te koppelen`}
            size={256}
            marginSize={4}
            level="M"
          />
          <p>{t`Of typ deze code in de app:`}</p>
          <p className="code">{pairing.code}</p>
          <p role="timer">{t`Deze code werkt nog ${formatClock(secondsLeft)} minuten.`}</p>
        </div>
      ) : null}

      {expired ? (
        <div className="pairing" ref={result} tabIndex={-1}>
          <p>{t`Deze code is verlopen.`}</p>
          <Button className="button" onPress={() => create.mutate()} isPending={create.isPending}>
            {t`Nieuwe code`}
          </Button>
        </div>
      ) : null}
    </Shell>
  );
}

export const pairAppRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/koppel-app",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: PairAppPage,
});
