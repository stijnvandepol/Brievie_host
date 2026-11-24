import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { Button, Form } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert, Field, Split, usePageTitle } from "../components/page";
import { SignInCode } from "../components/sign-in-code";
import { requireVisitor } from "../guards";
import { rootRoute } from "./root";

type User = components["schemas"]["UserOut"];

function LoginPage() {
  usePageTitle(t`Inloggen`);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // A second step follows the password: the code from the app, or setting it up.
  const [step, setStep] = useState<string | null>(null);

  async function signedIn(user: User) {
    queryClient.setQueryData(meQuery.queryKey, user);
    await navigate({ to: "/" });
  }

  const login = useMutation({
    mutationFn: async (credentials: { email: string; password: string }) => {
      const { data, error } = await api.POST("/api/v1/auth/login", { body: credentials });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (result) => {
      if ("step" in result) {
        setStep(result.step);
        return;
      }
      await signedIn(result);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    login.mutate({ email: String(form.get("email")), password: String(form.get("password")) });
  }

  if (step !== null) {
    return (
      <Split>
        <SignInCode
          step={step}
          onSignedIn={signedIn}
          onRestart={() => {
            login.reset();
            setStep(null);
          }}
        />
      </Split>
    );
  }

  return (
    <Split>
      <div className="heading">
        <h1>{t`Inloggen`}</h1>
        <p className="lede">{t`Gebruik het account van deze server.`}</p>
      </div>
      <Form onSubmit={submit} className="form">
        <Field label={t`E-mailadres`} name="email" type="email" autoComplete="username" />
        <Field
          label={t`Wachtwoord`}
          name="password"
          type="password"
          autoComplete="current-password"
        />
        <ErrorAlert error={login.error} />
        <Button type="submit" className="button" isPending={login.isPending}>
          {t`Inloggen`}
        </Button>
      </Form>
      <p className="hint">{t`Na vijf mislukte pogingen is inloggen tijdelijk geblokkeerd.`}</p>
    </Split>
  );
}

export const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  beforeLoad: ({ context }) => requireVisitor(context.queryClient, "login"),
  component: LoginPage,
});
