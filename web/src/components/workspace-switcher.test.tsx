import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import {
  documentHandlers,
  loggedInHandlers,
  renderApp,
  secondWorkspace,
  server,
  twoWorkspaces,
  workspace,
  workspaceHandlers,
} from "../test-utils";

afterEach(() => window.localStorage.clear());

const path = `/w/${workspace.id}/archief`;

function setUp() {
  server.use(
    twoWorkspaces(),
    ...loggedInHandlers(),
    ...documentHandlers(),
    ...workspaceHandlers(secondWorkspace.id),
  );
}

describe("workspace switcher", () => {
  it("with one workspace shows its name and no button", async () => {
    // The dead click of the old "Werkruimtes" (spec S 2.1) cannot come back.
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await within(nav).findByRole("link", { name: "Archief" });
    expect(within(nav).getAllByText("Thuis").length).toBeGreaterThan(0);
    expect(within(nav).queryByRole("button", { name: /Werkruimte/ })).not.toBeInTheDocument();
  });

  it("lists the workspaces without counts and marks the current", async () => {
    setUp();
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: /Werkruimte: Thuis/ }));
    const menu = await screen.findByRole("menu");
    const current = within(menu).getByRole("menuitemradio", { name: /Thuis/ });
    const other = within(menu).getByRole("menuitemradio", { name: /Bedrijf/ });
    expect(current).toHaveAttribute("aria-checked", "true");
    expect(other).toHaveAttribute("aria-checked", "false");
    // Kind and role, never a number of another workspace.
    expect(other).toHaveTextContent("Organisatie");
    expect(other).toHaveTextContent("Lid");
    expect(other.textContent ?? "").not.toMatch(/\d/);
    expect(within(menu).getByRole("menuitem", { name: "Alle werkruimtes" })).toBeVisible();
  });

  it("goes to the other workspace's Mijn post", async () => {
    setUp();
    const u = userEvent.setup();
    const { router } = renderApp(path);
    await u.click(await screen.findByRole("button", { name: /Werkruimte: Thuis/ }));
    await u.click(await screen.findByRole("menuitemradio", { name: /Bedrijf/ }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`/w/${secondWorkspace.id}/mijn-post`),
    );
    expect(await screen.findByRole("button", { name: /Werkruimte: Bedrijf/ })).toBeVisible();
  });

  it("choosing the current workspace only closes the menu", async () => {
    setUp();
    const u = userEvent.setup();
    const { router } = renderApp(path);
    await u.click(await screen.findByRole("button", { name: /Werkruimte: Thuis/ }));
    await u.click(await screen.findByRole("menuitemradio", { name: /Thuis/ }));
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe(path);
  });

  it("opens Alle werkruimtes", async () => {
    setUp();
    const u = userEvent.setup();
    const { router } = renderApp(path);
    await u.click(await screen.findByRole("button", { name: /Werkruimte: Thuis/ }));
    await u.click(await screen.findByRole("menuitem", { name: "Alle werkruimtes" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/werkruimtes"));
  });

  it("the brand link opens Mijn post of this workspace", async () => {
    setUp();
    renderApp(path);
    expect(await screen.findByRole("link", { name: "Brievie, naar Mijn post" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/mijn-post`,
    );
  });

  it("the page title names the workspace when there are several", async () => {
    setUp();
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Archief" });
    await waitFor(() => expect(document.title).toBe("Archief · Thuis - Brievie"));
  });

  it("the page title stays plain with one workspace", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Archief" });
    await waitFor(() => expect(document.title).toBe("Archief - Brievie"));
  });
});
