import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogTrigger,
  FieldError,
  Input,
  Label,
  Popover,
  Text,
  TextField,
} from "react-aria-components";
import { ApiFailure, api, errorMessage } from "../api/client";
import {
  inboxCountsQuery,
  meQuery,
  myTaskCountsQuery,
  visibilityQuery,
  workspacesQuery,
} from "../api/queries";
import { resetSearch } from "../search-term";
import { clearUndo } from "../undo-trash";
import { Icon, type IconName } from "./icons";
import { LanguageSwitch } from "./language-switch";
import { NoticeBar } from "./notice-bar";
import { SearchField } from "./search-field";
import { initials, MessageBar } from "./ui";
import { UndoBar } from "./undo-bar";

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
      <div className="plain-card">{children}</div>
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
        <LanguageSwitch />
        <div className={wide ? "split-content wide" : "split-content"}>{children}</div>
      </main>
    </div>
  );
}

/**
 * The title of a page, with what sits above it (a breadcrumb or a caption) and a command
 * bar below it: the main action first, the others as quieter buttons.
 */
export function PageHeader({
  title,
  eyebrow,
  breadcrumb,
  description,
  commands,
}: {
  title: string;
  eyebrow?: string;
  breadcrumb?: { to: BreadcrumbTarget; params: { workspaceId: string }; label: string };
  description?: ReactNode;
  commands?: ReactNode;
}) {
  return (
    <div className="page-top">
      <header className="page-header">
        {breadcrumb ? (
          <nav aria-label={t`Kruimelpad`} className="breadcrumb">
            <Link to={breadcrumb.to} params={breadcrumb.params} className="breadcrumb-link">
              {breadcrumb.label}
            </Link>
            <Icon name="chevronRight" className="breadcrumb-sep" />
          </nav>
        ) : null}
        {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
        <h1>{title}</h1>
        {description ? <p className="page-description">{description}</p> : null}
      </header>
      {commands ? <div className="command-bar">{commands}</div> : null}
    </div>
  );
}

type BreadcrumbTarget =
  | "/w/$workspaceId/inbox"
  | "/w/$workspaceId/post"
  | "/w/$workspaceId/automatisering"
  | "/w/$workspaceId/leden"
  | "/w/$workspaceId/instellingen";

const NAV_LINK = "nav-link";

/** A link in the navigation pane: an icon and its name (the name stays readable when only the icon shows). */
function NavItem({
  to,
  params,
  icon,
  label,
  exact = false,
  ariaLabel,
  count,
  warn = false,
}: {
  to:
    | "/"
    | "/koppel-app"
    | "/w/$workspaceId/taken"
    | "/w/$workspaceId/inbox"
    | "/w/$workspaceId/post"
    | "/w/$workspaceId/prullenbak"
    | "/w/$workspaceId/personen"
    | "/w/$workspaceId/afdelingen"
    | "/w/$workspaceId/leden"
    | "/w/$workspaceId/instellingen"
    | "/w/$workspaceId/labels"
    | "/w/$workspaceId/automatisering"
    | "/w/$workspaceId/ai";
  params?: { workspaceId: string };
  icon: IconName;
  label: string;
  exact?: boolean;
  ariaLabel?: string;
  count?: number;
  /** The count in the warning style (something is late); the label says it in words. */
  warn?: boolean;
}) {
  return (
    <Link
      to={to}
      params={params}
      activeOptions={{ exact }}
      className={NAV_LINK}
      aria-label={ariaLabel}
    >
      <Icon name={icon} className="nav-icon" />
      <span className="nav-label">{label}</span>
      {count ? (
        <span className={warn ? "nav-count nav-count-warning" : "nav-count"} aria-hidden="true">
          {count}
        </span>
      ) : null}
    </Link>
  );
}

/** The workspace a page belongs to, as far as the navigation needs it. */
type NavWorkspace = { id: string; name: string; role?: string };

/** The entries of one workspace; "Inbox" (route `inbox`) counts what waits for you (review
 * plus unassigned; a letter still being read waits for nobody).
 * A viewer may not read the rules, so Automatisering is left out for them. */
function WorkspaceNav({ workspace }: { workspace: NavWorkspace }) {
  const { data: counts } = useQuery(inboxCountsQuery(workspace.id));
  const { data: taskCounts } = useQuery(myTaskCountsQuery(workspace.id));
  const { data: sight } = useQuery(visibilityQuery(workspace.id));
  const todo = counts ? counts.review + counts.unassigned : 0;
  const mine = taskCounts?.mine ?? 0;
  const late = taskCounts?.overdue ?? 0;
  const params = { workspaceId: workspace.id };
  return (
    <>
      <p className="nav-section">
        <span className="nav-section-name">{workspace.name}</span>
      </p>
      <NavItem
        to="/w/$workspaceId/taken"
        params={params}
        icon="checklist"
        label={t`Mijn taken`}
        ariaLabel={
          mine > 0
            ? late > 0
              ? t`Mijn taken, ${mine} open, waarvan ${late} te laat`
              : t`Mijn taken, ${mine} open`
            : undefined
        }
        count={mine}
        warn={late > 0}
      />
      <NavItem
        to="/w/$workspaceId/inbox"
        params={params}
        icon="inbox"
        label={t`Inbox`}
        ariaLabel={
          todo > 0 ? plural(todo, { one: "Inbox, # brief", other: "Inbox, # brieven" }) : undefined
        }
        count={todo}
      />
      <NavItem to="/w/$workspaceId/post" params={params} icon="mail" label={t`Alle post`} />
      <NavItem
        to="/w/$workspaceId/prullenbak"
        params={params}
        icon="delete"
        label={t`Prullenbak`}
      />
      <NavItem to="/w/$workspaceId/personen" params={params} icon="people" label={t`Personen`} />
      {sight?.departments_enabled ? (
        <NavItem
          to="/w/$workspaceId/afdelingen"
          params={params}
          icon="people"
          label={sight.kind === "home" ? t`Groepen` : t`Afdelingen`}
        />
      ) : null}
      <NavItem to="/w/$workspaceId/leden" params={params} icon="personAdd" label={t`Leden`} />
      <NavItem to="/w/$workspaceId/labels" params={params} icon="tag" label={t`Labels`} />
      {workspace.role === "viewer" ? null : (
        <NavItem
          to="/w/$workspaceId/automatisering"
          params={params}
          icon="flash"
          label={t`Automatisering`}
        />
      )}
      <NavItem to="/w/$workspaceId/ai" params={params} icon="lightbulb" label={t`AI`} />
      {workspace.role === "admin" ? (
        <NavItem
          to="/w/$workspaceId/instellingen"
          params={params}
          icon="settings"
          label={t`Instellingen`}
        />
      ) : null}
      <hr className="nav-divider" />
    </>
  );
}

const WIDE = "(min-width: 1200px)";
const NAV_STORAGE_KEY = "brievie.nav-collapsed";

function matches(query: string): boolean {
  return typeof window.matchMedia === "function" ? window.matchMedia(query).matches : true;
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(NAV_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function storeCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(NAV_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // Storage blocked (private window): the choice lasts until the next page.
  }
}

/**
 * Whether the navigation pane shows its names. Wide screens show the full pane unless the
 * user folded it to icons (remembered in this browser). Narrower screens show icons only, or
 * nothing on a phone, and open the full pane over the page on request.
 */
function useNavPane() {
  const [wide, setWide] = useState(() => matches(WIDE));
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [overlay, setOverlay] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(WIDE);
    const update = () => {
      setWide(query.matches);
      setOverlay(false);
    };
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const expanded = wide ? !collapsed : overlay;
  const close = useCallback(() => setOverlay(false), []);
  function toggle() {
    if (wide) {
      storeCollapsed(!collapsed);
      setCollapsed(!collapsed);
    } else {
      setOverlay(!overlay);
    }
  }
  return { expanded, overlay: !wide && overlay, toggle, close };
}

/** Who is logged in, behind their initials in the app bar; the menu holds "Uitloggen". */
function AccountMenu({
  me,
  onLogout,
}: {
  me: { name: string; email: string; is_server_admin?: boolean } | null | undefined;
  onLogout: () => void;
}) {
  return (
    <DialogTrigger>
      <Button className="topbar-button account-button" aria-label={t`Account`}>
        <span className="avatar" aria-hidden="true">
          {me ? initials(me.name) : ""}
        </span>
        {me ? <span className="account-name">{me.name}</span> : null}
        <Icon name="chevronDown" className="account-chevron" />
      </Button>
      <Popover className="popover account-popover" placement="bottom end" offset={4}>
        <Dialog className="account-dialog" aria-label={t`Account`}>
          {me ? (
            <div className="account-card">
              <span className="avatar avatar-large" aria-hidden="true">
                {initials(me.name)}
              </span>
              <div className="account-who">
                <span className="account-card-name">{me.name}</span>
                <span className="account-email">{me.email}</span>
              </div>
            </div>
          ) : null}
          <Link to="/account/sessies" className="menu-item">
            <Icon name="grid" />
            {t`Waar je bent ingelogd`}
          </Link>
          <Link to="/account/wachtwoord" className="menu-item">
            <Icon name="edit" />
            {t`Wachtwoord wijzigen`}
          </Link>
          <Link to="/account/taal" className="menu-item">
            <Icon name="globe" />
            {t`Taal`}
          </Link>
          {me?.is_server_admin ? (
            <Link to="/server/logboek" className="menu-item">
              <Icon name="document" />
              {t`Serverlogboek`}
            </Link>
          ) : null}
          <Button className="menu-item" onPress={onLogout}>
            <Icon name="signOut" />
            {t`Uitloggen`}
          </Button>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

/** Layout of the screens after login: app bar, navigation pane, then the page. */
export function Shell({ children, workspace }: { children: ReactNode; workspace?: NavWorkspace }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: me } = useQuery(meQuery);
  // With exactly one workspace there is nothing to choose (`/` goes straight to it), so the
  // entry is left out. Hidden while loading, so it never shows and then disappears.
  const workspaces = useQuery(workspacesQuery);
  const showWorkspaces = workspaces.data ? workspaces.data.length !== 1 : workspaces.isError;
  const nav = useNavPane();
  const navId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);

  const logout = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/v1/auth/logout");
      // 401: the session was already gone, which is what we wanted.
      if (!response.ok && response.status !== 401) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      queryClient.clear();
      resetSearch();
      clearUndo();
      await navigate({ to: "/login" });
    },
  });

  // A pane opened over the page takes focus; Escape closes it and returns to the menu button.
  const { overlay, close } = nav;
  useEffect(() => {
    if (!overlay) return;
    paneRef.current?.querySelector<HTMLElement>("a")?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      close();
      toggleRef.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [overlay, close]);

  return (
    <div className="app" data-nav={nav.expanded ? "expanded" : "collapsed"}>
      <header className="topbar">
        <Button
          ref={toggleRef}
          className="topbar-button nav-toggle"
          aria-label={t`Menu`}
          aria-expanded={nav.expanded}
          aria-controls={navId}
          onPress={nav.toggle}
        >
          <Icon name="menu" />
        </Button>
        <Link to="/" className="brand">
          <BrandMark />
          <span>Brievie</span>
        </Link>
        {workspace ? <SearchField key={workspace.id} workspaceId={workspace.id} /> : null}
        <div className="topbar-end">
          <AccountMenu me={me} onLogout={() => logout.mutate()} />
        </div>
      </header>
      <div className="app-body">
        <div className="navpane" id={navId} ref={paneRef}>
          {/* A pane over the page closes by itself: every page change draws a new shell. */}
          <nav aria-label={t`Hoofdmenu`} className="nav">
            {workspace ? <WorkspaceNav workspace={workspace} /> : null}
            {showWorkspaces ? <NavItem to="/" exact icon="grid" label={t`Werkruimtes`} /> : null}
            <NavItem to="/koppel-app" icon="phone" label={t`Apparaten`} />
          </nav>
        </div>
        {overlay ? (
          // A pointer shortcut; Escape and the menu button close the pane by keyboard.
          <div className="scrim" aria-hidden="true" onClick={close} />
        ) : null}
        <main className="content">
          <div className="content-inner">
            <ErrorAlert error={logout.error} />
            <NoticeBar />
            {workspace ? <UndoBar workspaceId={workspace.id} /> : null}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

/** A failed call, announced to screen readers as it appears. */
export function ErrorAlert({ error }: { error: unknown }) {
  if (error === null || error === undefined) return null;
  return (
    <MessageBar intent="error" role="alert">
      {errorMessage(error)}
    </MessageBar>
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
