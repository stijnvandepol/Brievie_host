import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
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
import { ApiFailure, api, errorMessage, serverClockOffset } from "../api/client";
import { menuCountsQuery, meQuery, visibilityQuery } from "../api/queries";
import { resetSearch } from "../search-term";
import { clearUndo } from "../undo-trash";
import { Icon, type IconName } from "./icons";
import { LanguageSwitch } from "./language-switch";
import { NoticeBar } from "./notice-bar";
import { SearchField } from "./search-field";
import { initials, MessageBar } from "./ui";
import { UndoBar } from "./undo-bar";
import { WorkspaceSwitcher } from "./workspace-switcher";

/** The workspace a page's title names, when the user has several (set by the workspace
 * route): "Mijn post · Bedrijf - Brievie". Empty otherwise. */
const TitleSuffix = createContext("");

export function PageTitleSuffix({ value, children }: { value: string; children: ReactNode }) {
  return <TitleSuffix.Provider value={value}>{children}</TitleSuffix.Provider>;
}

/** Every page gets its own title (WCAG 2.4.2). */
export function usePageTitle(title: string) {
  const suffix = useContext(TitleSuffix);
  useEffect(() => {
    document.title = suffix ? `${title} · ${suffix} - Brievie` : `${title} - Brievie`;
  }, [title, suffix]);
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
  | "/w/$workspaceId/mensen"
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
  extra,
}: {
  to:
    | "/werkruimtes"
    | "/koppel-app"
    | "/w/$workspaceId/mijn-post"
    | "/w/$workspaceId/taken"
    | "/w/$workspaceId/inbox"
    | "/w/$workspaceId/post"
    | "/w/$workspaceId/prullenbak"
    | "/w/$workspaceId/mensen"
    | "/w/$workspaceId/afdelingen"
    | "/w/$workspaceId/instellingen"
    | "/w/$workspaceId/labels"
    | "/w/$workspaceId/automatisering";
  params?: { workspaceId: string };
  icon: IconName;
  label: string;
  exact?: boolean;
  ariaLabel?: string;
  count?: number;
  /** The count in the warning style (something is late); the label says it in words. */
  warn?: boolean;
  /** A second number in the warning style ("blijven liggen"); the label says it in words. */
  extra?: number;
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
      {extra ? (
        <span className="nav-count nav-count-warning" aria-hidden="true">
          {extra}
        </span>
      ) : null}
    </Link>
  );
}

/** The workspace a page belongs to, as far as the navigation needs it. */
type NavWorkspace = { id: string; name: string; kind?: string; role?: string };

/** The words of the Inbox entry: what waits, and what lies too long (spec S 4.1, S 6.2). */
function inboxLabel(waiting: number, lingering: number): string | undefined {
  if (waiting === 0 && lingering === 0) return undefined;
  const letters = plural(waiting, { one: "# brief", other: "# brieven" });
  const late = plural(lingering, { one: "# blijft liggen", other: "# blijven liggen" });
  if (lingering === 0) return t`Inbox, ${letters}`;
  return t`Inbox, ${letters}, ${late}`;
}

/**
 * The menu of one workspace in the owner's order (spec S 4.1): Inbox, Mijn post, Mijn
 * taken, Alle post, Labels, Mensen, Afdelingen or Groepen (when on), Automatisering (not
 * for viewers), Prullenbak; Instellingen at the bottom, for everyone. One call gives the
 * numbers; a number shows only above zero, its words are in the link's name.
 */
function WorkspaceNav({ workspace }: { workspace: NavWorkspace }) {
  const { data: counts } = useQuery(menuCountsQuery(workspace.id));
  const { data: sight } = useQuery(visibilityQuery(workspace.id));
  const waiting = counts?.inbox ?? 0;
  const lingering = counts?.shows_lingering ? counts.lingering : 0;
  const unread = counts?.my_post_unread ?? 0;
  const mine = counts?.my_tasks ?? 0;
  const late = counts?.my_tasks_overdue ?? 0;
  const params = { workspaceId: workspace.id };
  return (
    <>
      <NavItem
        to="/w/$workspaceId/inbox"
        params={params}
        icon="inbox"
        label={t`Inbox`}
        ariaLabel={inboxLabel(waiting, lingering)}
        count={waiting}
        extra={lingering}
      />
      <NavItem
        to="/w/$workspaceId/mijn-post"
        params={params}
        icon="mail"
        label={t`Mijn post`}
        ariaLabel={
          unread > 0
            ? plural(unread, { one: "Mijn post, # ongelezen", other: "Mijn post, # ongelezen" })
            : undefined
        }
        count={unread}
      />
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
      <NavItem to="/w/$workspaceId/post" params={params} icon="layers" label={t`Alle post`} />
      <NavItem to="/w/$workspaceId/labels" params={params} icon="tag" label={t`Labels`} />
      <NavItem to="/w/$workspaceId/mensen" params={params} icon="people" label={t`Mensen`} />
      {sight?.departments_enabled ? (
        <NavItem
          to="/w/$workspaceId/afdelingen"
          params={params}
          icon="building"
          label={sight.kind === "home" ? t`Groepen` : t`Afdelingen`}
        />
      ) : null}
      {workspace.role === "viewer" ? null : (
        <NavItem
          to="/w/$workspaceId/automatisering"
          params={params}
          icon="flash"
          label={t`Automatisering`}
        />
      )}
      <NavItem
        to="/w/$workspaceId/prullenbak"
        params={params}
        icon="delete"
        label={t`Prullenbak`}
      />
      <div className="nav-bottom">
        <hr className="nav-divider" />
        <NavItem
          to="/w/$workspaceId/instellingen"
          params={params}
          icon="settings"
          label={t`Instellingen`}
        />
      </div>
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
  workspaceId,
}: {
  me: { name: string; email: string; is_server_admin?: boolean } | null | undefined;
  onLogout: () => void;
  /** The workspace of the page, for "Wie kan mijn post zien?". */
  workspaceId?: string;
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
          {workspaceId ? (
            <Link
              to="/w/$workspaceId/wie-ziet-mijn-post"
              params={{ workspaceId }}
              className="menu-item"
            >
              <Icon name="people" />
              {t`Wie kan mijn post zien?`}
            </Link>
          ) : null}
          <Link to="/account/sessies" className="menu-item">
            <Icon name="grid" />
            {t`Waar je bent ingelogd`}
          </Link>
          <Link to="/account/wachtwoord" className="menu-item">
            <Icon name="edit" />
            {t`Wachtwoord wijzigen`}
          </Link>
          <Link to="/account/beveiliging" className="menu-item">
            <Icon name="shield" />
            {t`Beveiliging`}
          </Link>
          <Link to="/account/taal" className="menu-item">
            <Icon name="globe" />
            {t`Taal`}
          </Link>
          {me?.is_server_admin ? (
            <>
              <Link to="/server/logboek" className="menu-item">
                <Icon name="document" />
                {t`Serverlogboek`}
              </Link>
              <Link to="/server/back-up" className="menu-item">
                <Icon name="save" />
                {t`Back-up`}
              </Link>
              <Link to="/server/herstelblad" className="menu-item">
                <Icon name="shield" />
                {t`Herstelblad`}
              </Link>
            </>
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
export function Shell({
  children,
  workspace,
  allNotices = false,
}: {
  children: ReactNode;
  workspace?: NavWorkspace;
  /** Every notice, also those about a workspace (the overview of workspaces). */
  allNotices?: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: me } = useQuery(meQuery);
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
        {workspace ? (
          // The start of this workspace, not `/` (which looked like a dead click, S 2.1).
          <Link
            to="/w/$workspaceId/mijn-post"
            params={{ workspaceId: workspace.id }}
            className="brand"
            aria-label={t`Brievie, naar Mijn post`}
          >
            <BrandMark />
            <span>Brievie</span>
          </Link>
        ) : (
          <Link to="/" className="brand">
            <BrandMark />
            <span>Brievie</span>
          </Link>
        )}
        {workspace ? <SearchField key={workspace.id} workspaceId={workspace.id} /> : null}
        <div className="topbar-end">
          <AccountMenu me={me} onLogout={() => logout.mutate()} workspaceId={workspace?.id} />
        </div>
      </header>
      <div className="app-body">
        <div className="navpane" id={navId} ref={paneRef}>
          {/* A pane over the page closes by itself: every page change draws a new shell. */}
          <nav aria-label={t`Hoofdmenu`} className="nav">
            {workspace ? <WorkspaceSwitcher current={workspace} /> : null}
            {workspace ? (
              <WorkspaceNav workspace={workspace} />
            ) : (
              <>
                <NavItem to="/werkruimtes" icon="grid" label={t`Werkruimtes`} />
                <NavItem to="/koppel-app" icon="phone" label={t`Apparaten`} />
              </>
            )}
          </nav>
        </div>
        {overlay ? (
          // A pointer shortcut; Escape and the menu button close the pane by keyboard.
          <div className="scrim" aria-hidden="true" onClick={close} />
        ) : null}
        <main className="content">
          <div className="content-inner">
            <ErrorAlert error={logout.error} />
            <NoticeBar workspaceId={workspace?.id} all={allNotices} />
            {me?.is_server_admin ? <ServerClockWarning /> : null}
            {workspace ? <UndoBar workspaceId={workspace.id} /> : null}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

// More than this apart, a code of an authenticator app may fail (the server accepts one
// 30-second step either side).
const CLOCK_TOLERANCE_MS = 30_000;

/** For a server admin: the server's clock and this browser's differ more than 30 seconds. */
function ServerClockWarning() {
  const offset = serverClockOffset();
  if (offset === null || Math.abs(offset) <= CLOCK_TOLERANCE_MS) return null;
  return (
    <MessageBar intent="warning" role="status">
      {t`De klok van de server loopt niet gelijk. Inloggen met een code kan dan mislukken.`}
    </MessageBar>
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
