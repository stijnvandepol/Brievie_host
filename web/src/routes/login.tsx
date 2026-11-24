import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import type { FormEvent } from "react";
import { Button, Form } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { meQuery } from "../api/queries";
import { ErrorAlert, Field, Split, usePageTitle } from "../components/page";
import { requireVisitor } from "../guards";
import { rootRoute } from "./root";

function LoginPage() {
  usePageTitle(t`Inloggen`);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const login = useMutation({
    mutationFn: async (credentials: { email: string; password: string }) => {
      const { data, error } = await api.POST("/api/v1/auth/login", { body: credentials });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (user) => {
      // Two-step verification: signing in continues with its code.
      if ("step" in user) return;
      queryClient.setQueryData(meQuery.queryKey, user);
      await navigate({ to: "/" });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    login.mutate({ email: String(form.get("email")), password: String(form.get("password")) });
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
