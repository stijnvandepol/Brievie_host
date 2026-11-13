import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect } from "react";
import { Button, FieldError, Input, Label, Text, TextField } from "react-aria-components";
import { ApiFailure, api, errorMessage } from "../api/client";

/** Every page gets its own title (WCAG 2.4.2). */
export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} - Brievie`;
  }, [title]);
}

/** Layout of the screens before login (setup, login). */
export function Plain({ children }: { children: ReactNode }) {
  return (
    <main className="page">
      <p className="brand">Brievie</p>
      {children}
    </main>
  );
}

/** Layout of the screens after login: brand, navigation and logging out. */
export function Shell({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const logout = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/v1/auth/logout");
      // 401: the session was already gone, which is what we wanted.
      if (!response.ok && response.status !== 401) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      queryClient.clear();
      await navigate({ to: "/login" });
    },
  });

  return (
    <>
      <header className="bar">
        <Link to="/" className="brand">
          Brievie
        </Link>
        <nav aria-label={t`Hoofdmenu`} className="bar-nav">
          <Link to="/">{t`Werkruimtes`}</Link>
          <Link to="/koppel-app">{t`App koppelen`}</Link>
          <Button className="button secondary" onPress={() => logout.mutate()}>
            {t`Uitloggen`}
          </Button>
        </nav>
      </header>
      <main className="page">
        <ErrorAlert error={logout.error} />
        {children}
      </main>
    </>
  );
}

/** A failed call, announced to screen readers as it appears. */
export function ErrorAlert({ error }: { error: unknown }) {
  if (error === null || error === undefined) return null;
  return (
    <div role="alert" className="alert">
      {errorMessage(error)}
    </div>
  );
}

export function Field({
  label,
  description,
  ...props
}: {
  label: string;
  description?: string;
  name: string;
  type?: "text" | "email" | "password";
  autoComplete?: string;
  maxLength?: number;
  minLength?: number;
}) {
  return (
    <TextField {...props} isRequired className="field">
      <Label>{label}</Label>
      {description ? <Text slot="description">{description}</Text> : null}
      <Input />
      <FieldError />
    </TextField>
  );
}
