import { t } from "@lingui/core/macro";
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Button, Form, Input, Label, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { ErrorAlert } from "./page";
import { CodeField, SecondFactorSetup } from "./second-factor-setup";

type User = components["schemas"]["UserOut"];

/**
 * The second step after the password (or an invitation, a reset, the setup) answered 202.
 * `code`: the six digits from the authenticator app, or a recovery code for a lost phone.
 * `setup`: two-step verification is required and must be switched on first. The browser is
 * signed in only after this step; `onSignedIn` gets the account.
 */
export function SignInCode({
  step,
  onSignedIn,
  onRestart,
}: {
  step: string;
  onSignedIn: (user: User) => void | Promise<void>;
  onRestart: () => void;
}) {
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState("");
  const verify = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const value = recovery ? code.trim() : code.replace(/\s/g, "");
      const { data, error } = await api.POST("/api/v1/auth/login/code", {
        body: recovery ? { code: "", recovery_code: value } : { code: value, recovery_code: "" },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (user) => {
      setCode("");
      await onSignedIn(user);
    },
  });
  const me = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.GET("/api/v1/me");
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (user) => onSignedIn(user),
  });
  const expired = verify.error instanceof ApiFailure && verify.error.code === "sign_in_expired";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    verify.mutate();
  }

  if (step === "setup") {
    return (
      <>
        <div className="heading">
          <h1>{t`Tweestapsverificatie aanzetten`}</h1>
          <p className="lede">{t`Op deze server is tweestapsverificatie verplicht. Zet het nu aan; daarna ben je ingelogd.`}</p>
        </div>
        <SecondFactorSetup pending onDone={() => me.mutate()} />
        <ErrorAlert error={me.error} />
      </>
    );
  }

  return (
    <>
      <div className="heading">
        <h1>{t`Code invullen`}</h1>
        <p className="lede">
          {recovery
            ? t`Typ een van je herstelcodes. Elke code werkt één keer.`
            : t`Typ de code uit je authenticator-app.`}
        </p>
      </div>
      <Form onSubmit={submit} className="form">
        {recovery ? (
          <TextField className="field" value={code} onChange={setCode} isRequired maxLength={30}>
            <Label>{t`Herstelcode`}</Label>
            <Input autoComplete="off" autoCapitalize="characters" spellCheck={false} />
          </TextField>
        ) : (
          <CodeField value={code} onChange={setCode} label={t`Code`} />
        )}
        <ErrorAlert error={verify.error} />
        {expired ? (
          <Button className="button" onPress={onRestart}>
            {t`Opnieuw inloggen`}
          </Button>
        ) : (
          <Button type="submit" className="button" isPending={verify.isPending}>
            {t`Inloggen`}
          </Button>
        )}
      </Form>
      <Button
        className="button-text"
        onPress={() => {
          setRecovery(!recovery);
          setCode("");
          verify.reset();
        }}
      >
        {recovery
          ? t`Toch een code uit de app gebruiken`
          : t`Telefoon kwijt? Gebruik een herstelcode.`}
      </Button>
    </>
  );
}
