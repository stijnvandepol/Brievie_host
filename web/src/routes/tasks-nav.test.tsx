import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  API,
  HttpResponse,
  http,
  loggedInHandlers,
  noTaskCounts,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;

function taskCounts(mine: number, overdue: number) {
  return http.get(`${base}/my-tasks/counts`, () =>
    HttpResponse.json({ ...noTaskCounts, mine, overdue }),
  );
}

function emptyList() {
  return http.get(`${base}/my-tasks`, () =>
    HttpResponse.json({
      items: [],
      today: "2026-11-21",
      has_person: true,
      truncated: false,
      waiting_letters: 0,
      split: false,
    }),
  );
}

describe("navigation", () => {
  it("nav shows Mijn taken first with its count", async () => {
    server.use(taskCounts(4, 0), ...loggedInHandlers(), emptyList());
    renderApp(`/w/${workspace.id}/taken`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    const link = await within(nav).findByRole("link", { name: "Mijn taken, 4 open" });
    expect(link).toHaveAttribute("href", `/w/${workspace.id}/taken`);
    const links = within(nav).getAllByRole("link");
    const first = links.findIndex((l) => l.getAttribute("href")?.startsWith(`/w/${workspace.id}`));
    expect(links[first]).toBe(link);
    expect(within(link).getByText("4")).not.toHaveClass("nav-count-warning");
  });

  it("the count's name says how many are late", async () => {
    server.use(taskCounts(4, 1), ...loggedInHandlers(), emptyList());
    renderApp(`/w/${workspace.id}/taken`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    const link = await within(nav).findByRole("link", {
      name: "Mijn taken, 4 open, waarvan 1 te laat",
    });
    expect(within(link).getByText("4")).toHaveClass("nav-count-warning");
  });

  it("has no count without open tasks", async () => {
    server.use(...loggedInHandlers(), emptyList());
    renderApp(`/w/${workspace.id}/taken`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Mijn taken" })).toBeInTheDocument();
  });
});
