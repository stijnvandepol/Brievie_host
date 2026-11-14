import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect } from "react";
import { Button, FieldError, Input, Label, Text, TextField } from "react-aria-components";
import { ApiFailure, api, errorMessage } from "../api/client";
import { meQuery } from "../api/queries";

/** Every page gets its own title (WCAG 2.4.2). */
export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} - Brievie`;
  }, [title]);
}

/** The envelope mark; decorative, the word next to it names the product. */
function BrandMark() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </svg>
  );
}

/** A small centred screen without navigation (router errors, not found). */
export function Plain({ children }: { children: ReactNode }) {
  return (
    <main className="plain">
      <p className="brand">
        <BrandMark />
        <span>Brievie</span>
      </p>
      {children}
    </main>
  );
}

/** Layout of the screens before login (setup, login): brand panel left, form right. */
export function Split({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="split">
      <aside className="split-aside">
        <p className="brand">
          <BrandMark />
          <span>Brievie</span>
        </p>
        <div className="split-statement">
          <p className="split-title">{t`Je post en facturen, op je eigen server.`}</p>
          <p className="split-sub">{t`Documenten verlaten je netwerk niet.`}</p>
        </div>
        <p className="split-host">{t`Server: ${window.location.host}`}</p>
      </aside>
      <main className="split-main">
        <div className={wide ? "split-content wide" : "split-content"}>{children}</div>
      </main>
    </div>
  );
}

const NAV_LINK = "nav-link";

/** Layout of the screens after login: sidebar with navigation and account, then the page. */
export function Shell({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: me } = useQuery(meQuery);

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
    <div className="shell">
      <div className="sidebar">
        <Link to="/" className="brand">
          <BrandMark />
          <span>Brievie</span>
        </Link>
        <nav aria-label={t`Hoofdmenu`} className="sidebar-nav">
          <Link to="/" activeOptions={{ exact: true }} className={NAV_LINK}>
            {t`Werkruimtes`}
          </Link>
          <Link to="/koppel-app" className={NAV_LINK}>
            {t`Apparaten`}
          </Link>
        </nav>
        <div className="sidebar-account">
          {me ? (
            <>
              <span className="account-name">{me.name}</span>
              <span className="account-email">{me.email}</span>
            </>
          ) : null}
          <Button className="sidebar-button" onPress={() => logout.mutate()}>
            {t`Uitloggen`}
          </Button>
        </div>
      </div>
      <main className="main">
        <div className="main-inner">
          <ErrorAlert error={logout.error} />
          {children}
        </div>
      </main>
    </div>
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
  /** For codes typed on a phone: no capital letters, no autocorrect, no spell check. */
  literal?: boolean;
}) {
  const { literal, ...fieldProps } = props;
  return (
    <TextField {...fieldProps} isRequired className="field">
      <Label>{label}</Label>
      {description ? <Text slot="description">{description}</Text> : null}
      <Input
        {...(literal ? { autoCapitalize: "none", autoCorrect: "off", spellCheck: false } : {})}
      />
      <FieldError />
    </TextField>
  );
}
