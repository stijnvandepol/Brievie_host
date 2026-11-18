import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { API, HttpResponse, http, loggedInHandlers, renderApp, server, user } from "../test-utils";

const serverAdmin = { ...user, is_server_admin: true };
const log = [
  {
    id: "00000000-0000-4000-8000-000000000001",
    created_at: "2026-11-17T10:00:00Z",
    actor_kind: "system",
    actor_id: null,
    actor_name: "",
    actor_device_id: null,
    code: "sign_in_failed",
    label: "Inloggen mislukt",
    target_kind: "user",
    target_id: null,
    target_name: "",
    values: {},
    client_ip: "203.0.113.9",
  },
];

describe("the server log", () => {
  it("is only in a server admin's menu", async () => {
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json(serverAdmin)),
      ...loggedInHandlers(),
    );
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).getByRole("link", { name: "Serverlogboek" })).toHaveAttribute(
      "href",
      "/server/logboek",
    );
  });

  it("is not in anybody else's menu", async () => {
    server.use(...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).queryByRole("link", { name: "Serverlogboek" })).not.toBeInTheDocument();
  });

  it("shows the server's events with a CSV link", async () => {
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json(serverAdmin)),
      ...loggedInHandlers(),
      http.get(`*${API}/server/audit-events`, () => HttpResponse.json(log)),
    );
    renderApp("/server/logboek");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Serverlogboek" }),
    ).toBeInTheDocument();
    const row = await screen.findByRole("row", { name: /Inloggen mislukt/ });
    expect(within(row).getByText("203.0.113.9")).toBeInTheDocument();
    expect(within(row).getByText("Systeem")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download als CSV" })).toHaveAttribute(
      "href",
      "/api/v1/server/audit-events/csv",
    );
    // No list of accounts exists for the whole server: no person filter here.
    expect(screen.queryByRole("combobox", { name: "Persoon" })).not.toBeInTheDocument();
  });

  it("sends anybody else away", async () => {
    server.use(...loggedInHandlers());
    renderApp("/server/logboek");
    await screen.findByRole("heading", { level: 1, name: "Werkruimtes" }).catch(() => undefined);
    expect(screen.queryByRole("heading", { name: "Serverlogboek" })).not.toBeInTheDocument();
  });
});
