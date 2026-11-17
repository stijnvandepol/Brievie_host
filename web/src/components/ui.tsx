import { t } from "@lingui/core/macro";
import { type ReactNode, useEffect, useState } from "react";
import { Icon, type IconName } from "./icons";

/** The four kinds of message and status, each with its own icon: colour is never the only signal. */
export type Intent = "info" | "success" | "warning" | "error";

const INTENT_ICON: Record<Intent, IconName> = {
  info: "info",
  success: "success",
  warning: "warning",
  error: "error",
};

/**
 * A message bar: a tinted strip with an icon and a sentence. `role` is only set where the
 * message must be announced as it appears (an error is an `alert`).
 */
export function MessageBar({
  intent,
  role,
  children,
}: {
  intent: Intent;
  role?: "alert" | "status";
  children: ReactNode;
}) {
  return (
    <div role={role} className={`message-bar message-${intent}`}>
      <Icon name={INTENT_ICON[intent]} className="message-icon" />
      <div className="message-body">{children}</div>
    </div>
  );
}

/** A small tag for a status or role; `intent` adds the tint and the icon. */
export function Badge({
  intent,
  icon,
  children,
}: {
  intent?: Intent | "brand";
  icon?: IconName;
  children: ReactNode;
}) {
  const shown = icon ?? (intent && intent !== "brand" ? INTENT_ICON[intent] : undefined);
  return (
    <span className={intent ? `badge badge-${intent}` : "badge"}>
      {shown ? <Icon name={shown} className="badge-icon" /> : null}
      {children}
    </span>
  );
}

/** "Even laden..." with a spinner; the text carries the meaning, the spinner is decoration. */
export function Loading() {
  return (
    <p className="loading">
      <span className="spinner" aria-hidden="true" />
      {t`Even laden...`}
    </p>
  );
}

/** An empty list: an icon, what is (not) there, and optionally what to do about it. */
export function EmptyState({
  icon,
  children,
  hint,
}: {
  icon: IconName;
  children: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <Icon name={icon} className="empty-icon" />
      <p className="empty-title">{children}</p>
      {hint ? <p className="empty-hint">{hint}</p> : null}
    </div>
  );
}

/** Up to two initials of a name: "Anna de Vries" gives "AV", "Papa" gives "P". */
export function initials(name: string): string {
  const words = name
    .split(/\s+/)
    .filter((word) => word !== "" && word[0] === word[0]?.toUpperCase());
  const picked = words.length > 1 ? [words[0], words[words.length - 1]] : words.slice(0, 1);
  const letters = picked.map((word) => word?.[0] ?? "").join("");
  return letters || name.trim().slice(0, 1).toUpperCase();
}

/** The colour of a persona is fixed per name, so a person keeps their colour everywhere. */
function personaTone(name: string): number {
  let sum = 0;
  for (const char of name) sum = (sum + (char.codePointAt(0) ?? 0)) % 997;
  return sum % 6;
}

/** A person: initials in a circle and the name as text (the name is what is read out). */
export function Persona({ name }: { name: string }) {
  return (
    <span className="persona">
      <span className={`persona-avatar tone-${personaTone(name)}`} aria-hidden="true">
        {initials(name)}
      </span>
      <span className="persona-name">{name}</span>
    </span>
  );
}

/** A row of persona chips, or nothing. */
export function Personas({ people }: { people: { id: string; name: string }[] }) {
  if (people.length === 0) return null;
  return (
    <span className="personas">
      {people.map((person) => (
        <Persona key={person.id} name={person.name} />
      ))}
    </span>
  );
}

/** Whether a media query matches, kept up to date; `fallback` where the browser cannot say (tests). */
export function useMediaQuery(query: string, fallback = false): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window.matchMedia === "function" ? window.matchMedia(query).matches : fallback,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}
