import { t } from "@lingui/core/macro";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Button } from "react-aria-components";
import { requestSearch, useSearchRequest } from "../search-term";
import { Icon } from "./icons";
import { useMediaQuery } from "./ui";

const PHONE = "(max-width: 639px)";

/** Whether a key press belongs to a field the user is typing in. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    target.isContentEditable
  );
}

/**
 * The search field of the app bar. "/" focuses it from anywhere (not while typing in a field),
 * Enter shows the results, Escape clears it and gives focus back to where it was. On a phone
 * it is an icon button that opens the field over the bar.
 */
export function SearchField({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const phone = useMediaQuery(PHONE);
  const onResults = useRouterState({
    select: (state) => state.location.pathname.endsWith("/zoeken"),
  });
  const { term: shown } = useSearchRequest();
  const [value, setValue] = useState(onResults ? shown : "");
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // The last element outside the field that had focus: Escape returns there.
  const before = useRef<HTMLElement | null>(null);
  const wantFocus = useRef(false);

  useEffect(() => {
    function onFocusIn(event: FocusEvent) {
      if (event.target instanceof HTMLElement && event.target !== input.current) {
        before.current = event.target;
      }
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.defaultPrevented || isTyping(event.target)) return;
      event.preventDefault();
      if (input.current) input.current.focus();
      else {
        wantFocus.current = true;
        setOpen(true);
      }
    }
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const showField = !phone || open;
  useEffect(() => {
    if (showField && wantFocus.current) {
      wantFocus.current = false;
      input.current?.focus();
    }
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    requestSearch(value.trim());
    void navigate({ to: "/w/$workspaceId/zoeken", params: { workspaceId } });
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Escape") return;
    event.preventDefault();
    setValue("");
    const target = before.current;
    if (target?.isConnected) target.focus();
    else input.current?.blur();
    if (phone) setOpen(false);
  }

  if (!showField) {
    return (
      <Button
        className="topbar-button"
        aria-label={t`Zoeken`}
        onPress={() => {
          wantFocus.current = true;
          setOpen(true);
        }}
      >
        <Icon name="search" />
      </Button>
    );
  }

  return (
    <search className={phone ? "search-field search-field-over" : "search-field"}>
      <form className="search-form" onSubmit={submit}>
        <Icon name="search" className="search-icon" />
        <input
          ref={input}
          type="search"
          name="q"
          className="search-input"
          aria-label={t`Zoeken in je post`}
          placeholder={t`Zoek op afzender, onderwerp of tekst`}
          autoComplete="off"
          maxLength={200}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => {
            if (phone && value === "") setOpen(false);
          }}
        />
      </form>
    </search>
  );
}
