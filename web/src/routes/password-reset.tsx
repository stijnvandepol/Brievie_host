import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button, Form, Input, Label, Text, TextField } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery, workspacesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { useFragmentToken } from "../components/fragment-token";
import { ErrorAlert, Split, usePageTitle } from "../components/page";
import { SignInCode } from "../components/sign-in-code";
import { MessageBar } from "../components/ui";
import { rootRoute } from "./root";

function PasswordResetPage() {
  usePageTitle(t`Nieuw wachtwoord kiezen`);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [mismatch, setMismatch] = useState(false);
  // The password is set; with two-step verification the sign-in waits for the code.
  const [step, setStep] = useState<string | null>(null);

  useFragmentToken(setToken);

  async function signedIn(user: components["schemas"]["UserOut"]) {
    // Completing a reset signs in.
    queryClient.setQueryData(meQuery.queryKey, user);
    await queryClient.invalidateQueries({ queryKey: workspacesQuery.queryKey });
    await navigate({ to: "/" });
  }

  const reset = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/auth/password-reset", {
        body: { token: token ?? "", password },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (result) => {
      if ("step" in result) {
        setPassword("");
        setAgain("");
        setStep(result.step);
        return;
      }
      await signedIn(result);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== again) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    reset.mutate();
  }

  if (step !== null) {
    return (
      <Split>
        <SignInCode
          step={step}
          onSignedIn={signedIn}
          onRestart={() => void navigate({ to: "/login" })}
        />
      </Split>
    );
  }

  return (
    <Split>
      {token === "" ? (
        <MessageBar intent="error" role="alert">
          {t`In deze link zit geen herstelcode. Open de link uit je bericht opnieuw.`}
        </MessageBar>
      ) : null}
      {token ? (
        <>
          <div className="heading">
            <h1>{t`Nieuw wachtwoord kiezen`}</h1>
            <p className="lede">{t`Daarna ben je ingelogd. Je moet je telefoon opnieuw koppelen.`}</p>
          </div>
          <Form onSubmit={submit} className="form">
            <TextField
              type="password"
              className="field"
              value={password}
              onChange={setPassword}
              isRequired
            >
              <Label>{t`Nieuw wachtwoord`}</Label>
              <Text slot="description">{t`Kies minstens 12 tekens.`}</Text>
              <Input autoComplete="new-password" />
            </TextField>
            <TextField
              type="password"
              className="field"
              value={again}
              onChange={setAgain}
              isRequired
            >
              <Label>{t`Herhaal nieuw wachtwoord`}</Label>
              <Input autoComplete="new-password" />
            </TextField>
            {mismatch ? (
              <MessageBar intent="error" role="alert">
                {t`De twee wachtwoorden zijn niet hetzelfde.`}
              </MessageBar>
            ) : null}
            <ErrorAlert error={reset.error} />
            <Button type="submit" className="button" isPending={reset.isPending}>
              {t`Wachtwoord opslaan`}
            </Button>
          </Form>
        </>
      ) : null}
    </Split>
  );
}

export const passwordResetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/wachtwoord-herstellen",
  component: PasswordResetPage,
});
