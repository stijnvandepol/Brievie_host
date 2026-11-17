import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useId } from "react";
import { Button, FieldError, Input, Label, Text, TextField } from "react-aria-components";
import { ApiFailure, api, errorMessage } from "../api/client";
import { inboxCountsQuery, meQuery } from "../api/queries";

/** Every page gets its own title (WCAG 2.4.2). */
export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} - Brievie`;
  }, [title]);
}

/** Moves keyboard and screen reader focus to the page's title (`h1`), for example after
 * the element that had focus went away with a deleted row. */
export function focusPageHeading() {
  const heading = document.querySelector("h1");
  if (heading) {
    heading.tabIndex = -1;
    heading.focus();
  }
}

/** The envelope mark; decorative, the word next to it names the product. */
function BrandMark() {
  // The tick is the flap of the envelope: it is punched out where it crosses the envelope
  // (mask, so it works on any background) and solid where its tail runs past it.
  const id = useId().replace(/:/g, "");
  const maskId = `${id}-punch`;
  const clipId = `${id}-envelope`;
  const tick = "M232 472 L456 664 L836 260";
  return (
    <svg aria-hidden="true" focusable="false" className="brand-mark" viewBox="144 216 736 592">
      <defs>
        <clipPath id={clipId}>
          <rect x="156" y="396" width="600" height="400" rx="52" />
        </clipPath>
        <mask id={maskId} maskUnits="userSpaceOnUse" x="144" y="216" width="736" height="592">
          <rect x="144" y="216" width="736" height="592" fill="#fff" />
          <path
            d={tick}
            fill="none"
            stroke="#000"
            strokeWidth="64"
            strokeLinecap="round"
            strokeLinejoin="round"
            clipPath={`url(#${clipId})`}
          />
        </mask>
      </defs>
      <g mask={`url(#${maskId})`} fill="currentColor">
        <path
          d={tick}
          fill="none"
          stroke="currentColor"
          strokeWidth="64"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <rect x="156" y="396" width="600" height="400" rx="52" />
      </g>
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

/** The entries of one workspace; the inbox shows what waits for you (review plus unassigned). */
function WorkspaceNav({ workspace }: { workspace: { id: string; name: string } }) {
  const { data: counts } = useQuery(inboxCountsQuery(workspace.id));
  const todo = counts ? counts.review + counts.unassigned : 0;
  const params = { workspaceId: workspace.id };
  return (
    <>
      <p className="sidebar-workspace">{workspace.name}</p>
      <Link
        to="/w/$workspaceId/inbox"
        params={params}
        className={NAV_LINK}
        aria-label={todo > 0 ? t`Inbox, ${todo} te doen` : undefined}
      >
        {t`Inbox`}
        {todo > 0 ? (
          <span className="nav-count" aria-hidden="true">
            {todo}
          </span>
        ) : null}
      </Link>
      <Link to="/w/$workspaceId/post" params={params} className={NAV_LINK}>
        {t`Post`}
      </Link>
      <Link to="/w/$workspaceId/personen" params={params} className={NAV_LINK}>
        {t`Personen`}
      </Link>
      <Link to="/w/$workspaceId/labels" params={params} className={NAV_LINK}>
        {t`Labels`}
      </Link>
      <Link to="/w/$workspaceId/automatisering" params={params} className={NAV_LINK}>
        {t`Automatisering`}
      </Link>
      <hr className="sidebar-rule" />
    </>
  );
}

/** Layout of the screens after login: sidebar with navigation and account, then the page. */
export function Shell({
  children,
  workspace,
}: {
  children: ReactNode;
  workspace?: { id: string; name: string };
}) {
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
          {workspace ? <WorkspaceNav workspace={workspace} /> : null}
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
