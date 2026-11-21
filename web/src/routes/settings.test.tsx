import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  asRole,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/instellingen`;
const base = `*${API}/workspaces/${workspace.id}`;

describe("settings", () => {
  it("an admin switches member export", async () => {
    let body: unknown = null;
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/settings`, () => HttpResponse.json({ allow_member_export: true })),
      http.put(`${base}/settings`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ allow_member_export: false });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    const toggle = await screen.findByRole("switch", {
      name: "Leden mogen hun eigen post exporteren",
    });
    expect(toggle).toBeChecked();
    await u.click(toggle);
    expect(await screen.findByRole("status")).toHaveTextContent("Opgeslagen");
    expect(body).toEqual({ allow_member_export: false });
    expect(toggle).not.toBeChecked();
  });

  it("links to the log", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/settings`, () => HttpResponse.json({ allow_member_export: true })),
    );
    renderApp(path);
    expect(await screen.findByRole("link", { name: "Logboek" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/instellingen/logboek`,
    );
  });

  it("shows the server's sentence and keeps the old value when saving fails", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/settings`, () => HttpResponse.json({ allow_member_export: true })),
      http.put(`${base}/settings`, () =>
        HttpResponse.json(errorBody("forbidden", "Alleen een beheerder mag dit doen."), {
          status: 403,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    const toggle = await screen.findByRole("switch", {
      name: "Leden mogen hun eigen post exporteren",
    });
    await u.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Alleen een beheerder mag dit doen.",
    );
    expect(
      await screen.findByRole("switch", { name: "Leden mogen hun eigen post exporteren" }),
    ).toBeChecked();
  });

  it("is in the navigation for an admin only", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/settings`, () => HttpResponse.json({ allow_member_export: true })),
    );
    renderApp(path);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(within(nav).getByRole("link", { name: "Instellingen" })).toBeInTheDocument();
  });

  it("a member has Instellingen without the workspace's settings", async () => {
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      http.get(`${base}/documents`, () => HttpResponse.json([])),
      http.get(`${base}/persons`, () => HttpResponse.json([])),
      http.get(`${base}/labels`, () => HttpResponse.json([])),
      http.get(`${base}/ai`, () => HttpResponse.json({ mode: "off", has_api_key: false })),
    );
    renderApp(path);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Instellingen" })).toHaveAttribute(
      "href",
      path,
    );
    expect(await screen.findByRole("heading", { level: 2, name: "Jouw account" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Apparaten" })).toHaveAttribute("href", "/koppel-app");
    expect(screen.getAllByRole("link", { name: "Wie kan mijn post zien?" })[0]).toHaveAttribute(
      "href",
      `/w/${workspace.id}/wie-ziet-mijn-post`,
    );
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Logboek" })).not.toBeInTheDocument();
  });

  it("an admin changes the thresholds in an organisation only", async () => {
    const bodies: unknown[] = [];
    const current = {
      allow_member_export: false,
      lingering_enabled: true,
      lingering_inbox_days: 2,
      lingering_unopened_days: 5,
    };
    server.use(
      asRole("admin", "business"),
      ...loggedInHandlers(),
      http.get(`${base}/settings`, () => HttpResponse.json(current)),
      http.put(`${base}/settings`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        bodies.push(body);
        return HttpResponse.json({ ...current, ...body });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    const section = await screen.findByRole("region", { name: "Blijft liggen" });
    expect(
      within(section).getByRole("switch", { name: "Laat zien welke post blijft liggen" }),
    ).toBeChecked();
    const inbox = within(section).getByRole("spinbutton", { name: "Dagen in de Inbox" });
    await u.clear(inbox);
    await u.type(inbox, "3");
    await u.tab();
    expect(bodies).toEqual([{ lingering_inbox_days: 3 }]);
    const unopened = within(section).getByRole("spinbutton", { name: "Dagen niet geopend" });
    await u.clear(unopened);
    await u.type(unopened, "99");
    await u.tab();
    expect(within(section).getByRole("alert")).toHaveTextContent(
      "Kies een aantal dagen van 1 tot 60.",
    );
    expect(bodies).toHaveLength(1);
  });

  it("a home workspace has no Blijft liggen", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/settings`, () => HttpResponse.json({ allow_member_export: true })),
    );
    renderApp(path);
    await screen.findByRole("switch", { name: "Leden mogen hun eigen post exporteren" });
    expect(screen.queryByRole("region", { name: "Blijft liggen" })).not.toBeInTheDocument();
  });
});
