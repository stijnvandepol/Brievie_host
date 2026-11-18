import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { type FormEvent, useState } from "react";
import {
  Button,
  Form,
  Input,
  Label,
  Radio,
  RadioGroup,
  Text,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery, workspacesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { AiStatusSentence } from "../components/ai-status-line";
import { formatTimeLeft, useSecondsLeft } from "../components/countdown";
import { useFragmentToken } from "../components/fragment-token";
import { Icon } from "../components/icons";
import { ErrorAlert, Split, usePageTitle } from "../components/page";
import { Loading, MessageBar, useMediaQuery } from "../components/ui";
import { rootRoute } from "./root";

type Info = components["schemas"]["InvitationInfoOut"];
type Pairing = components["schemas"]["PairingCodeOut"];
type Account = "new" | "existing";

function roleSentence(role: string): string {
  if (role === "admin") return t`Je wordt beheerder: je kunt leden uitnodigen en alles instellen.`;
  if (role === "viewer") return t`Je wordt meekijker: je kunt alles lezen, maar niets wijzigen.`;
  return t`Je wordt lid: je kunt post scannen, controleren en ordenen.`;
}

/** The one sentence for a link that does not work; the server sends the same one. */
function deadLink(): string {
  return t`Deze uitnodiging werkt niet meer. Vraag een nieuwe.`;
}

/** After joining: the app, as a link on a phone and as a QR code on a computer. */
function ContinueInApp({
  workspaceName,
  pairing,
  error,
  isPending,
  onNewCode,
}: {
  workspaceName: string;
  pairing: Pairing | undefined;
  error: unknown;
  isPending: boolean;
  onNewCode: () => void;
}) {
  const touch = useMediaQuery("(pointer: coarse)");
  const secondsLeft = useSecondsLeft(pairing?.expires_at);
  const expired = pairing !== undefined && secondsLeft === 0;
  return (
    <>
      <div className="heading">
        <h1>{t`Welkom bij ${workspaceName}`}</h1>
        <p className="lede">{t`Je bent nu lid. Koppel ook de Brievie-app op je telefoon, dan scan je brieven met de app.`}</p>
      </div>
      <ErrorAlert error={error} />
      {isPending ? <Loading /> : null}
      {pairing !== undefined && !expired ? (
        <div className="invite-app">
          {touch ? (
            <>
              <a className="button" href={pairing.server_link}>
                <Icon name="phone" />
                {t`Open de Brievie-app`}
              </a>
              <p className="muted">{t`Lukt dat niet? Typ deze code in de app.`}</p>
              <p className="num code">{pairing.code}</p>
              <p className="muted small" role="timer">
                {t`(nog ${formatTimeLeft(secondsLeft)})`}
              </p>
            </>
          ) : (
            <>
              <div className="invite-qr">
                <QRCodeSVG
                  value={pairing.qr_payload}
                  title={t`QR-code om de app te koppelen`}
                  size={200}
                  marginSize={4}
                  level="M"
                />
              </div>
              <p>{t`Scan deze code met de Brievie-app`}</p>
              <p className="muted small" role="timer">
                {t`(nog ${formatTimeLeft(secondsLeft)})`}
              </p>
            </>
          )}
        </div>
      ) : null}
      {expired ? (
        <MessageBar intent="warning" role="status">
          {t`Deze code is verlopen.`}
        </MessageBar>
      ) : null}
      <div className="invite-continue">
        {expired || (pairing === undefined && error !== null && error !== undefined) ? (
          <Button className="button secondary" onPress={onNewCode}>
            <Icon name="refresh" />
            {t`Nieuwe code`}
          </Button>
        ) : null}
        <Link to="/" className="button">
          {t`Naar Brievie`}
        </Link>
      </div>
    </>
  );
}

function AcceptForm({ token, info }: { token: string; info: Info }) {
  const queryClient = useQueryClient();
  const [account, setAccount] = useState<Account>("new");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const pair = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/devices/pairing-codes");
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  const accept = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/invitations/accept", {
        body: {
          token,
          account,
          name: account === "new" ? name.trim() : "",
          email: email.trim(),
          password,
        },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (data) => {
      // Accepting signs in: the rest of the app asks the server who this is.
      queryClient.setQueryData(meQuery.queryKey, data.user);
      void queryClient.invalidateQueries({ queryKey: workspacesQuery.queryKey });
      setPassword("");
      pair.mutate();
    },
    onError: (failure) => {
      // The address has an account: the form moves to the other choice, nothing is lost.
      if (failure instanceof ApiFailure && failure.code === "account_exists")
        setAccount("existing");
    },
  });

  if (accept.data) {
    return (
      <ContinueInApp
        workspaceName={accept.data.workspace.name}
        pairing={pair.data}
        error={pair.error}
        isPending={pair.isPending}
        onNewCode={() => pair.mutate()}
      />
    );
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    accept.mutate();
  }

  return (
    <>
      <div className="heading">
        <h1>{t`${info.invited_by} nodigt je uit voor ${info.workspace_name}`}</h1>
        <p className="lede">{roleSentence(info.role)}</p>
        {info.person_name ? (
          <p className="muted">{t`Deze uitnodiging is voor ${info.person_name}.`}</p>
        ) : null}
      </div>
      {info.ai.mode !== "off" ? <AiStatusSentence status={info.ai} /> : null}
      <Form onSubmit={submit} className="form">
        <RadioGroup
          className="choice"
          value={account}
          onChange={(value) => setAccount(value === "existing" ? "existing" : "new")}
        >
          <Label className="choice-label">{t`Heb je al een account?`}</Label>
          <div className="cards cards-stack">
            <Radio value="new" className="card" aria-label={t`Nieuw account`}>
              <span className="card-top" aria-hidden="true">
                <strong>{t`Nieuw account`}</strong>
                <span className="radio-indicator" />
              </span>
              <span>{t`Je maakt een account op deze server.`}</span>
            </Radio>
            <Radio value="existing" className="card" aria-label={t`Ik heb al een account`}>
              <span className="card-top" aria-hidden="true">
                <strong>{t`Ik heb al een account`}</strong>
                <span className="radio-indicator" />
              </span>
              <span>{t`Je logt in met je account van deze server.`}</span>
            </Radio>
          </div>
        </RadioGroup>
        {account === "new" ? (
          <TextField className="field" value={name} onChange={setName} isRequired maxLength={150}>
            <Label>{t`Naam`}</Label>
            <Input autoComplete="name" />
          </TextField>
        ) : null}
        <TextField type="email" className="field" value={email} onChange={setEmail} isRequired>
          <Label>{t`E-mailadres`}</Label>
          {info.email_fixed ? (
            <Text slot="description">
              {t`Deze uitnodiging is voor één e-mailadres. Vul dat adres in.`}
            </Text>
          ) : null}
          <Input autoComplete="username" />
        </TextField>
        <TextField
          type="password"
          className="field"
          value={password}
          onChange={setPassword}
          isRequired
        >
          <Label>{t`Wachtwoord`}</Label>
          {account === "new" ? <Text slot="description">{t`Kies minstens 12 tekens.`}</Text> : null}
          <Input autoComplete={account === "new" ? "new-password" : "current-password"} />
        </TextField>
        <ErrorAlert error={accept.error} />
        <Button type="submit" className="button" isPending={accept.isPending}>
          {account === "new" ? t`Account maken en meedoen` : t`Inloggen en meedoen`}
        </Button>
      </Form>
    </>
  );
}

function InvitationPage() {
  usePageTitle(t`Uitnodiging`);
  const [token, setToken] = useState<string | null>(null);
  const lookup = useMutation({
    gcTime: 0,
    mutationFn: async (value: string) => {
      const { data, error } = await api.POST("/api/v1/invitations/lookup", {
        body: { token: value },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
  });
  const { mutate } = lookup;

  useFragmentToken((value) => {
    setToken(value);
    if (value !== "") mutate(value);
  });

  return (
    <Split>
      {token === "" ? (
        <MessageBar intent="error" role="alert">
          {deadLink()}
        </MessageBar>
      ) : null}
      {token === null || lookup.isPending ? <Loading /> : null}
      {lookup.error ? <ErrorAlert error={lookup.error} /> : null}
      {lookup.data && token ? <AcceptForm token={token} info={lookup.data} /> : null}
    </Split>
  );
}

export const invitationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/uitnodiging",
  component: InvitationPage,
});
