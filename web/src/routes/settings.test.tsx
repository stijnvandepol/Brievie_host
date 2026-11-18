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
    const toggle = await screen.findByRole("switch");
    await u.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Alleen een beheerder mag dit doen.",
    );
    expect(await screen.findByRole("switch")).toBeChecked();
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

  it("a member has no Instellingen and is sent away from the page", async () => {
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
    expect(within(nav).queryByRole("link", { name: "Instellingen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});
