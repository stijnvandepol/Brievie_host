import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import {
  API,
  documentHandlers,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  secondWorkspace,
  server,
  twoWorkspaces,
  user,
  workspace,
  workspaceHandlers,
} from "../test-utils";

afterEach(() => window.localStorage.clear());

describe("start", () => {
  it("/ goes to Mijn post of the last workspace used, else the first", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers(), ...documentHandlers());
    const first = renderApp("/");
    await waitFor(() =>
      expect(first.router.state.location.pathname).toBe(`/w/${workspace.id}/mijn-post`),
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Mijn post" })).toBeVisible();
    cleanup();

    window.localStorage.setItem("brievie.last-workspace", secondWorkspace.id);
    server.use(...workspaceHandlers(secondWorkspace.id));
    const again = renderApp("/");
    await waitFor(() =>
      expect(again.router.state.location.pathname).toBe(`/w/${secondWorkspace.id}/mijn-post`),
    );
    cleanup();

    // A workspace the user left is not opened again.
    window.localStorage.setItem("brievie.last-workspace", "00000000-0000-4000-8000-000000000000");
    const gone = renderApp("/");
    await waitFor(() =>
      expect(gone.router.state.location.pathname).toBe(`/w/${workspace.id}/mijn-post`),
    );
  });

  it("/ goes to /werkruimtes without workspaces", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([])),
      ...loggedInHandlers(),
    );
    const { router } = renderApp("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/werkruimtes"));
    expect(await screen.findByText("Je hoort nog bij geen werkruimte.")).toBeVisible();
  });

  it("remembers the workspace opened last", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers(), ...workspaceHandlers(secondWorkspace.id));
    renderApp(`/w/${secondWorkspace.id}/post`);
    await screen.findByRole("heading", { level: 1, name: "Alle post" });
    expect(window.localStorage.getItem("brievie.last-workspace")).toBe(secondWorkspace.id);
  });
});

describe("Alle werkruimtes", () => {
  it("lists the workspaces in a table with kind and role", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/werkruimtes");
    expect(
      await screen.findByRole("heading", { level: 1, name: /werkruimtes/i }),
    ).toBeInTheDocument();
    const row = (await screen.findByRole("rowheader", { name: "Thuis" })).closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement).getAllByRole("cell");
    expect(cells.map((cell) => cell.textContent)).toEqual(["Thuis", "Beheerder"]);
    expect(screen.getByRole("columnheader", { name: "Rol" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Apparaten" })).toHaveAttribute("href", "/koppel-app");
  });

  it("also lists a single workspace: the page never bounces", async () => {
    server.use(...loggedInHandlers());
    const { router } = renderApp("/werkruimtes");
    expect(await screen.findByRole("rowheader", { name: "Thuis" })).toBeVisible();
    expect(router.state.location.pathname).toBe("/werkruimtes");
  });

  it("marks the current page in the menu and shows who is logged in", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/werkruimtes");
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Werkruimtes" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // The name is in the app bar; the e-mail address is in the account menu.
    expect(await screen.findByText("Anna")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).getByText("anna@example.nl")).toBeInTheDocument();
  });

  it("links each workspace to its Mijn post", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp("/werkruimtes");
    expect(await screen.findByRole("link", { name: "Bedrijf" })).toHaveAttribute(
      "href",
      `/w/${secondWorkspace.id}/mijn-post`,
    );
  });

  it("logs out with the CSRF token and returns to the login screen", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    document.cookie = "csrftoken=tok123; path=/";
    let csrf: string | null = null;
    server.use(
      http.post(`*${API}/auth/logout`, ({ request }) => {
        csrf = request.headers.get("x-csrftoken");
        server.use(
          http.get(`*${API}/me`, () =>
            HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), {
              status: 401,
            }),
          ),
        );
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    const { router } = renderApp("/werkruimtes");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    await u.click(await screen.findByRole("button", { name: /uitloggen/i }));
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeInTheDocument();
    expect(csrf).toBe("tok123");
    expect(router.state.location.pathname).toBe("/login");
  });

  it("goes to the login screen when the session ended while the page was open", async () => {
    const notLoggedIn = () =>
      HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 });
    let asked = 0;
    server.use(
      // The guard still sees a user; once the page asks for data the session is gone.
      http.get(`*${API}/me`, () => {
        asked += 1;
        return asked === 1 ? HttpResponse.json(user) : notLoggedIn();
      }),
      http.get(`*${API}/workspaces`, notLoggedIn),
      ...loggedInHandlers(),
    );
    const { router } = renderApp("/werkruimtes");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeVisible();
  });
});
