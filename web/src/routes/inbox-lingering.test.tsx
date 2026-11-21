import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  menuCounts,
  papa,
  renderApp,
  server,
  workspace,
} from "../test-utils";

type Lingering = components["schemas"]["LingeringOut"];

const path = `/w/${workspace.id}/inbox`;
const base = `*${API}/workspaces/${workspace.id}`;

const lingering: Lingering = {
  too_long_in_inbox: [
    { ...documentSummary, id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b6661", sender: "Gemeente", days: 4 },
  ],
  not_opened: [
    {
      ...documentSummary,
      id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b6662",
      sender: "Leverancier BV",
      addressee_ids: [papa.id],
      days: 5,
    },
  ],
  inbox_days: 2,
  unopened_days: 5,
};

function setUp(answer: Lingering, shows = true) {
  let asked = 0;
  server.use(
    menuCounts({ shows_lingering: shows, lingering: 2 }),
    http.get(`${base}/inbox/lingering`, () => {
      asked += 1;
      return HttpResponse.json(answer);
    }),
    http.get(`${base}/documents`, () => HttpResponse.json([])),
    ...loggedInHandlers(),
    ...documentHandlers(),
  );
  return () => asked;
}

describe("Blijft liggen", () => {
  it("sorters see the two groups with ages and addressees", async () => {
    setUp(lingering);
    renderApp(path);
    const section = await screen.findByRole("region", { name: /Blijft liggen/ });
    expect(
      await within(section).findByRole("heading", {
        name: "In de Inbox te lang (2 dagen of meer)",
      }),
    ).toBeVisible();
    expect(within(section).getByRole("link", { name: "Gemeente" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/post/${lingering.too_long_in_inbox[0]?.id}`,
    );
    expect(within(section).getByText("ligt er 4 dagen")).toBeVisible();
    expect(
      within(section).getByRole("heading", { name: "Nog niet geopend (5 dagen of meer)" }),
    ).toBeVisible();
    expect(within(section).getByText("5 dagen niet geopend")).toBeVisible();
    expect(await within(section).findByText("Papa")).toBeVisible();
  });

  it("plain members and home workspaces see nothing of it", async () => {
    const asked = setUp(lingering, false);
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Inbox" });
    // The menu's numbers decide: wait until they are in (the Inbox entry is drawn).
    await screen.findByRole("link", { name: "Inbox" });
    expect(screen.queryByRole("region", { name: /Blijft liggen/ })).not.toBeInTheDocument();
    expect(asked()).toBe(0);
  });

  it("the section collapses to one line when empty", async () => {
    setUp({ ...lingering, too_long_in_inbox: [], not_opened: [] });
    renderApp(path);
    const section = await screen.findByRole("region", { name: /Blijft liggen/ });
    expect(await within(section).findByText("Er blijft niets liggen.")).toBeVisible();
    expect(within(section).queryByRole("list")).not.toBeInTheDocument();
  });
});
