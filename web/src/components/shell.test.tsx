import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API,
  asRole,
  documentHandlers,
  HttpResponse,
  homeVisibility,
  http,
  loggedInHandlers,
  menuCounts,
  noMenuCounts,
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
    renderApp("/werkruimtes");
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
    renderApp("/werkruimtes");
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
  it("is not in a workspace's menu: the switcher on top takes its place", async () => {
    server.use(twoWorkspaces(), ...loggedInHandlers());
    renderApp(`/w/${workspace.id}/mensen`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await within(nav).findByRole("link", { name: "Inbox" });
    expect(within(nav).queryByRole("link", { name: "Werkruimtes" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: /Werkruimte: Thuis/ })).toBeInTheDocument();
  });

  it("is shown outside a workspace, also with one workspace", async () => {
    server.use(...loggedInHandlers());
    renderApp("/koppel-app");
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Werkruimtes" })).toHaveAttribute(
      "href",
      "/werkruimtes",
    );
  });
});

describe("the menu in the owner's order", () => {
  function names(nav: HTMLElement): string[] {
    return within(nav)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith(`/w/${workspace.id}/`))
      .map((link) => link.querySelector(".nav-label")?.textContent ?? "");
  }

  /** The menu of a page for `role`; `first` answers before the usual handlers. */
  async function menuOf(
    role: "admin" | "member" | "viewer",
    kind: "home" | "business" = "home",
    first: Parameters<typeof server.use> = [],
  ) {
    server.use(...first, asRole(role, kind), ...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await within(nav).findByRole("link", { name: "Instellingen" });
    return nav;
  }

  it("lists the entries in the owner's order", async () => {
    const order = [
      "Inbox",
      "Mijn post",
      "Mijn taken",
      "Alle post",
      "Labels",
      "Mensen",
      "Automatisering",
      "Prullenbak",
      "Instellingen",
    ];
    expect(names(await menuOf("admin"))).toEqual(order);
    cleanup();
    expect(names(await menuOf("member"))).toEqual(order);
    cleanup();
    // A viewer may not read the rules.
    expect(names(await menuOf("viewer"))).toEqual(order.filter((n) => n !== "Automatisering"));
  });

  it("puts Afdelingen after Mensen when departments are on", async () => {
    const nav = await menuOf("member", "business", [
      http.get(`*${API}/workspaces/${workspace.id}/visibility`, () =>
        HttpResponse.json({ ...homeVisibility, kind: "business", departments_enabled: true }),
      ),
    ]);
    await within(nav).findByRole("link", { name: "Afdelingen" });
    const list = names(nav);
    expect(list.indexOf("Afdelingen")).toBe(list.indexOf("Mensen") + 1);
  });

  it("Inbox counts waiting letters and says lingering ones in words", async () => {
    const nav = await menuOf("admin", "business", [
      menuCounts({ inbox: 3, lingering: 2, shows_lingering: true }),
    ]);
    const inbox = await within(nav).findByRole("link", {
      name: "Inbox, 3 brieven, 2 blijven liggen",
    });
    expect(within(inbox).getByText("2")).toHaveClass("nav-count-warning");
    expect(within(inbox).getByText("3")).not.toHaveClass("nav-count-warning");
  });

  it("Mijn post counts unread letters", async () => {
    const nav = await menuOf("member", "home", [menuCounts({ my_post_unread: 4 })]);
    expect(
      await within(nav).findByRole("link", { name: "Mijn post, 4 ongelezen" }),
    ).toHaveAttribute("href", `/w/${workspace.id}/mijn-post`);
  });

  it("Mijn taken warns in words when a task is late", async () => {
    const nav = await menuOf("member", "home", [menuCounts({ my_tasks: 2, my_tasks_overdue: 1 })]);
    const link = await within(nav).findByRole("link", {
      name: "Mijn taken, 2 open, waarvan 1 te laat",
    });
    expect(within(link).getByText("2")).toHaveClass("nav-count-warning");
  });

  it("asks one menu-counts call per page, not the old two", async () => {
    const asked: string[] = [];
    const first = [
      http.get(`*${API}/workspaces/${workspace.id}/menu/counts`, () => {
        asked.push("menu");
        return HttpResponse.json(noMenuCounts);
      }),
      http.get(`*${API}/workspaces/${workspace.id}/inbox/counts`, () => {
        asked.push("inbox");
        return HttpResponse.json({ new: 0, review: 0, unassigned: 0 });
      }),
      http.get(`*${API}/workspaces/${workspace.id}/my-tasks/counts`, () => {
        asked.push("tasks");
        return HttpResponse.json({});
      }),
    ];
    await menuOf("member", "home", first);
    await waitFor(() => expect(asked).toContain("menu"));
    expect(asked).toEqual(["menu"]);
  });

  it("Instellingen is last and open to members", async () => {
    const nav = await menuOf("member");
    const list = names(nav);
    expect(list[list.length - 1]).toBe("Instellingen");
    // AI and Apparaten moved under Instellingen (decision B6).
    expect(within(nav).queryByRole("link", { name: "AI" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Apparaten" })).not.toBeInTheDocument();
  });
});
