import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loggedInHandlers, renderApp, server, twoWorkspaces } from "../test-utils";

/** A screen narrower than the wide layout: `matchMedia` answers no to every query. */
function narrowScreen() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("navigation pane", () => {
  it("folds to icons on a wide screen and remembers that", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/");
    const toggle = await screen.findByRole("button", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await u.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // The names stay: a folded pane only hides them from sight.
    const nav = screen.getByRole("navigation", { name: "Hoofdmenu" });
    expect(within(nav).getByRole("link", { name: "Apparaten" })).toBeInTheDocument();

    await u.click(within(nav).getByRole("link", { name: "Apparaten" }));
    await screen.findByRole("heading", { level: 1, name: /app koppelen/i });
    expect(screen.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  });

  it("opens over the page on a narrow screen and closes with Escape", async () => {
    narrowScreen();
    server.use(twoWorkspaces(), ...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/");
    const toggle = await screen.findByRole("button", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await u.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const nav = screen.getByRole("navigation", { name: "Hoofdmenu" });
    await waitFor(() => expect(within(nav).getAllByRole("link")[0]).toHaveFocus());

    await u.keyboard("{Escape}");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });
});
