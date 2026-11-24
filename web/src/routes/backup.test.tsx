import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { API, HttpResponse, http, loggedInHandlers, renderApp, server, user } from "../test-utils";

const serverAdmin = { ...user, is_server_admin: true };

const quiet = {
  destination: "folder",
  last_success_at: "2026-11-23T02:31:00Z",
  last_success_size_bytes: 12_400_000,
  last_failure_at: null,
  last_failure_reason: "",
  last_check_at: null,
  running: false,
  late: false,
};

function asServerAdmin(status: object) {
  server.use(
    http.get(`*${API}/me`, () => HttpResponse.json(serverAdmin)),
    ...loggedInHandlers(),
    http.get(`*${API}/server/backup-status`, () => HttpResponse.json(status)),
  );
}

describe("the back-up panel", () => {
  it("is only in a server admin's menu", async () => {
    server.use(...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/koppel-app");
    await u.click(await screen.findByRole("button", { name: "Account" }));
    const menu = await screen.findByRole("dialog", { name: "Account" });
    expect(within(menu).queryByRole("link", { name: "Back-up" })).not.toBeInTheDocument();
  });

  it("shows the last back-up and a button", async () => {
    asServerAdmin(quiet);
    renderApp("/server/back-up");
    expect(await screen.findByRole("heading", { level: 1, name: "Back-up" })).toBeInTheDocument();
    expect(await screen.findByText("Laatste gelukte back-up")).toBeInTheDocument();
    expect(screen.getByText(/12,4 MB|12\.4 MB/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nu back-uppen" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says how to set it up when there is no destination", async () => {
    asServerAdmin({ ...quiet, destination: "none", last_success_at: null });
    renderApp("/server/back-up");
    expect(await screen.findByText(/Er is nog geen back-up ingesteld/)).toBeInTheDocument();
    expect(screen.getByText(/BRIEVIE_BACKUP_DIR=/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nu back-uppen" })).not.toBeInTheDocument();
  });

  it("warns when the last back-up is late and shows the server's reason", async () => {
    asServerAdmin({
      ...quiet,
      late: true,
      last_failure_reason: "De bestemming van de back-up is niet bereikbaar.",
    });
    renderApp("/server/back-up");
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.map((alert) => alert.textContent).join(" ")).toContain("meer dan anderhalve dag");
    expect(
      screen.getByText("De bestemming van de back-up is niet bereikbaar."),
    ).toBeInTheDocument();
  });

  it("starts a back-up and shows that one is running", async () => {
    let started = 0;
    let running = false;
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json(serverAdmin)),
      ...loggedInHandlers(),
      http.get(`*${API}/server/backup-status`, () => HttpResponse.json({ ...quiet, running })),
      http.post(`*${API}/server/backup-runs`, () => {
        started += 1;
        running = true;
        return HttpResponse.json(
          { run_id: "00000000-0000-4000-8000-000000000001" },
          { status: 202 },
        );
      }),
    );
    const u = userEvent.setup();
    renderApp("/server/back-up");
    await u.click(await screen.findByRole("button", { name: "Nu back-uppen" }));
    expect(await screen.findByText(/Er loopt een back-up/)).toBeInTheDocument();
    expect(started).toBe(1);
    expect(screen.getByRole("button", { name: "Nu back-uppen" })).toBeDisabled();
  });
});
