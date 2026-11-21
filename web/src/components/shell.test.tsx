import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API,
  gate,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  twoWorkspaces,
  workspace,
} from "../test-utils";

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
    await screen.findByRole("heading", { level: 1, name: "Apparaten" });
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

describe("Werkruimtes in the navigation", () => {
  it("is left out with exactly one workspace: there is nothing to choose", async () => {
    server.use(...loggedInHandlers());
    // A workspace page: its route has loaded the workspaces before the page shows.
    renderApp(`/w/${workspace.id}/personen`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await within(nav).findByRole("link", { name: "Inbox" });
    expect(within(nav).queryByRole("link", { name: "Werkruimtes" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Apparaten" })).toBeInTheDocument();
  });

  it("is shown with two workspaces", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/koppel-app");
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Werkruimtes" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("is shown with no workspace", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([])),
      ...loggedInHandlers(),
    );
    renderApp("/koppel-app");
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Werkruimtes" })).toBeInTheDocument();
  });

  it("does not flash in while the workspaces are loading", async () => {
    const answer = gate();
    let answered = false;
    server.use(
      http.get(`*${API}/workspaces`, async () => {
        await answer.opened;
        answered = true;
        return HttpResponse.json([workspace]);
      }),
      ...loggedInHandlers(),
    );
    renderApp("/koppel-app");
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await within(nav).findByRole("link", { name: "Apparaten" });
    expect(within(nav).queryByRole("link", { name: "Werkruimtes" })).not.toBeInTheDocument();
    answer.open();
    await waitFor(() => expect(answered).toBe(true));
    await within(nav).findByRole("link", { name: "Apparaten" });
    expect(within(nav).queryByRole("link", { name: "Werkruimtes" })).not.toBeInTheDocument();
  });
});
