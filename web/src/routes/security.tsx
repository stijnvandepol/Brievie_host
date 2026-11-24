import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button, Form, Input, Label, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery, mySecondFactorQuery, mySessionsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import {
  CodeField,
  RecoveryCodesDialog,
  ScanSteps,
  SecondFactorSetup,
} from "../components/second-factor-setup";
import { Badge, Loading, MessageBar } from "../components/ui";
import { dateFormat } from "../format";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

type Status = components["schemas"]["SecondFactorOut"];
type Mode = "idle" | "setup" | "renew" | "remove" | "phone";

const FEW_CODES = 2;
const day = dateFormat({ day: "numeric", month: "long" });

/** Who asks for two-step verification, in one sentence; empty when nobody does. */
function requirementSentence(status: Status): string {
  if (status.required_by === "server") return t`Op deze server is tweestapsverificatie verplicht.`;
  if (status.required_by === "workspace") {
    const names = status.required_by_workspaces.join(", ");
    return t`Werkruimte ${names} vraagt tweestapsverificatie.`;
  }
  return "";
}

function RenewCodes({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const renew = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/me/second-factor/recovery-codes", {
        body: { code: code.replace(/\s/g, "") },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data.recovery_codes;
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    renew.mutate();
  }

  if (renew.data) return <RecoveryCodesDialog codes={renew.data} onSaved={onDone} />;
  return (
    <Form onSubmit={submit} className="form form-narrow" aria-label={t`Nieuwe herstelcodes`}>
      <CodeField
        value={code}
        onChange={setCode}
        label={t`Code uit de app`}
        description={t`Je oude herstelcodes werken daarna niet meer.`}
      />
      <ErrorAlert error={renew.error} />
      <div>
        <Button type="submit" className="button" isPending={renew.isPending}>
          {t`Nieuwe herstelcodes maken`}
        </Button>
      </div>
    </Form>
  );
}

/** "Nieuwe telefoon" (spec S 7): a code from the old app (or a recovery code), then the
 * new app scans a new key; the old one works until the new one is confirmed. */
function NewPhone({ onDone }: { onDone: () => void }) {
  const [recovery, setRecovery] = useState(false);
  const [proof, setProof] = useState("");
  const [code, setCode] = useState("");
  const begin = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const value = recovery ? proof.trim() : proof.replace(/\s/g, "");
      const { data, error } = await api.POST("/api/v1/me/second-factor/replacement", {
        body: recovery ? { code: "", recovery_code: value } : { code: value, recovery_code: "" },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  const confirm = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/me/second-factor/replacement/confirm", {
        body: { code: code.replace(/\s/g, "") },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data.recovery_codes;
    },
  });

  function submitProof(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    begin.mutate();
  }

  function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    confirm.mutate();
  }

  if (confirm.data) return <RecoveryCodesDialog codes={confirm.data} onSaved={onDone} />;
  if (begin.data) {
    return (
      <section className="second-factor-setup" aria-labelledby="new-phone-title">
        <h3 id="new-phone-title">{t`Zet de nieuwe telefoon klaar`}</h3>
        <ScanSteps secret={begin.data} />
        <Form onSubmit={submitCode} className="form form-narrow">
          <CodeField value={code} onChange={setCode} label={t`Code uit de nieuwe app`} />
          <ErrorAlert error={confirm.error} />
          <div>
            <Button type="submit" className="button" isPending={confirm.isPending}>
              {t`Overzetten`}
            </Button>
          </div>
        </Form>
      </section>
    );
  }
  return (
    <Form onSubmit={submitProof} className="form form-narrow" aria-label={t`Nieuwe telefoon`}>
      <p>{t`Je oude telefoon blijft werken tot de nieuwe klaar is.`}</p>
      {recovery ? (
        <TextField className="field" value={proof} onChange={setProof} isRequired maxLength={30}>
          <Label>{t`Herstelcode`}</Label>
          <Input autoComplete="off" autoCapitalize="characters" spellCheck={false} />
        </TextField>
      ) : (
        <CodeField value={proof} onChange={setProof} label={t`Code uit de oude app`} />
      )}
      <ErrorAlert error={begin.error} />
      <div className="settings-links">
        <Button type="submit" className="button" isPending={begin.isPending}>
          {t`Verder`}
        </Button>
        <Button
          className="button-text"
          onPress={() => {
            setRecovery(!recovery);
            setProof("");
            begin.reset();
          }}
        >
          {recovery
            ? t`Toch een code uit de app gebruiken`
            : t`Oude telefoon kwijt? Gebruik een herstelcode.`}
        </Button>
      </div>
    </Form>
  );
}

function RemoveFactor({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const remove = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { error, response } = await api.DELETE("/api/v1/me/second-factor", {
        body: { password, code: code.replace(/\s/g, "") },
      });
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: onDone,
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    remove.mutate();
  }

  return (
    <Form onSubmit={submit} className="form form-narrow" aria-label={t`Uitzetten`}>
      <p>{t`Daarna is je wachtwoord weer genoeg om in te loggen. Je andere browsers worden uitgelogd.`}</p>
      <TextField
        type="password"
        className="field"
        value={password}
        onChange={setPassword}
        isRequired
      >
        <Label>{t`Wachtwoord`}</Label>
        <Input autoComplete="current-password" />
      </TextField>
      <CodeField value={code} onChange={setCode} label={t`Code uit de app`} />
      <ErrorAlert error={remove.error} />
      <div>
        <Button type="submit" className="button danger-solid" isPending={remove.isPending}>
          {t`Uitzetten`}
        </Button>
      </div>
    </Form>
  );
}

/** "Beveiliging" (spec S 9): your own two-step verification. */
function SecurityPage() {
  usePageTitle(t`Beveiliging`);
  const queryClient = useQueryClient();
  const status = useQuery(mySecondFactorQuery);
  const [mode, setMode] = useState<Mode>("idle");
  const [message, setMessage] = useState("");

  async function done(text: string) {
    setMode("idle");
    setMessage(text);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: mySecondFactorQuery.queryKey }),
      queryClient.invalidateQueries({ queryKey: meQuery.queryKey }),
      queryClient.invalidateQueries({ queryKey: mySessionsQuery.queryKey }),
    ]);
  }

  const data = status.data;
  const requirement = data ? requirementSentence(data) : "";
  return (
    <Shell>
      <PageHeader
        title={t`Beveiliging`}
        description={t`Met tweestapsverificatie vraagt Brievie na je wachtwoord een code van je telefoon. Zo kan niemand met alleen je wachtwoord bij je post.`}
      />
      {status.isPending ? <Loading /> : null}
      <ErrorAlert error={status.error} />
      {message ? (
        <MessageBar intent="success" role="status">
          {message}
        </MessageBar>
      ) : null}
      {data ? (
        <section className="panel panel-pad" aria-labelledby="two-step">
          <h2 id="two-step" className="cell-with-badge">
            {t`Tweestapsverificatie`}
            {data.enabled ? (
              <Badge intent="success" icon="checkmark">{t`Aan`}</Badge>
            ) : (
              <Badge>{t`Uit`}</Badge>
            )}
          </h2>
          <p>
            {data.enabled ? t`Tweestapsverificatie staat aan.` : t`Tweestapsverificatie staat uit.`}
          </p>
          {requirement ? <p>{requirement}</p> : null}
          {data.grace_until ? (
            <MessageBar intent="warning" role="status">
              {t`Zet tweestapsverificatie aan vóór ${day.format(new Date(data.grace_until))}. Daarna kun je pas inloggen als je het hebt aangezet.`}
            </MessageBar>
          ) : null}
          {data.enabled ? (
            data.recovery_codes_left <= FEW_CODES ? (
              <MessageBar intent="warning" role="status">
                {t`Je hebt nog ${data.recovery_codes_left} herstelcodes. Maak nieuwe aan.`}
              </MessageBar>
            ) : (
              <p className="muted">{t`Je hebt nog ${data.recovery_codes_left} herstelcodes.`}</p>
            )
          ) : null}

          {mode === "setup" ? (
            <SecondFactorSetup onDone={() => done(t`Tweestapsverificatie staat aan.`)} />
          ) : null}
          {mode === "renew" ? (
            <RenewCodes onDone={() => done(t`Je nieuwe herstelcodes zijn klaar.`)} />
          ) : null}
          {mode === "remove" ? (
            <RemoveFactor onDone={() => done(t`Tweestapsverificatie staat uit.`)} />
          ) : null}
          {mode === "phone" ? (
            <NewPhone
              onDone={() => done(t`Tweestapsverificatie staat nu op je nieuwe telefoon.`)}
            />
          ) : null}

          {mode === "idle" ? (
            <div className="settings-links">
              {data.enabled ? null : (
                <Button
                  className="button"
                  onPress={() => {
                    setMessage("");
                    setMode("setup");
                  }}
                >
                  {t`Aanzetten`}
                </Button>
              )}
              {data.enabled ? (
                <Button
                  className="button secondary"
                  onPress={() => {
                    setMessage("");
                    setMode("phone");
                  }}
                >
                  {t`Nieuwe telefoon`}
                </Button>
              ) : null}
              {data.enabled ? (
                <Button
                  className="button secondary"
                  onPress={() => {
                    setMessage("");
                    setMode("renew");
                  }}
                >
                  {t`Nieuwe herstelcodes`}
                </Button>
              ) : null}
              {data.enabled && !data.required ? (
                <Button
                  className="button secondary"
                  onPress={() => {
                    setMessage("");
                    setMode("remove");
                  }}
                >
                  {t`Uitzetten`}
                </Button>
              ) : null}
            </div>
          ) : (
            <div>
              <Button className="button-text" onPress={() => setMode("idle")}>
                {t`Annuleren`}
              </Button>
            </div>
          )}
          {data.enabled && data.required ? (
            <p className="muted">{t`Uitzetten kan niet: tweestapsverificatie is verplicht.`}</p>
          ) : null}
        </section>
      ) : null}
      <section className="panel panel-pad" aria-labelledby="security-devices">
        <h2 id="security-devices">{t`Gekoppelde telefoons`}</h2>
        <p className="muted">{t`Een gekoppelde telefoon blijft werken als je tweestapsverificatie aanzet. Wil je opnieuw beginnen? Ontkoppel hem bij Apparaten.`}</p>
        <div className="settings-links">
          <Link to="/koppel-app" className="button secondary">
            {t`Apparaten`}
          </Link>
        </div>
      </section>
    </Shell>
  );
}

export const securityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/account/beveiliging",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: SecurityPage,
});
