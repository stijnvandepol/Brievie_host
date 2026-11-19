import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  asRole,
  HttpResponse,
  http,
  loggedInHandlers,
  memberHandlers,
  members,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/instellingen/logboek`;
const base = `*${API}/workspaces/${workspace.id}`;
const bram = members[1] as (typeof members)[number];

type Event = components["schemas"]["AuditEventOut"];

function event(index: number, extra: Partial<Event> = {}): Event {
  const minute = String(59 - (index % 60)).padStart(2, "0");
  const hour = String(23 - Math.floor(index / 60)).padStart(2, "0");
  return {
    id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    created_at: `2026-11-17T${hour}:${minute}:00Z`,
    actor_kind: "user",
    actor_id: members[0]?.user_id ?? null,
    actor_name: "Anna",
    actor_device_id: null,
    code: "member_role_changed",
    label: "Rol gewijzigd",
    target_kind: "user",
    target_id: bram.user_id,
    target_name: "Bram",
    values: { from: "member", to: "viewer" },
    client_ip: "192.168.1.20",
    ...extra,
  };
}

describe("the log", () => {
  it("lists events with when, who, what, about whom and the address", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`${base}/audit-events`, () => HttpResponse.json([event(0)])),
    );
    renderApp(path);
    const row = (await screen.findByRole("row", { name: /Rol gewijzigd/ })) as HTMLElement;
    expect(within(row).getByText("Anna")).toBeInTheDocument();
    expect(within(row).getByText("Bram")).toBeInTheDocument();
    expect(within(row).getByText("192.168.1.20")).toBeInTheDocument();
    for (const name of ["Tijdstip", "Wie", "Gebeurtenis", "Om wie of wat", "Adres"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
  });

  it("filters by person and kind and loads more with before", async () => {
    const seen: URLSearchParams[] = [];
    const first = Array.from({ length: 100 }, (_, index) => event(index));
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`${base}/audit-events`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        seen.push(params);
        return HttpResponse.json(params.get("before") ? [event(500)] : first);
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await screen.findAllByRole("row", { name: /Rol gewijzigd/ });
    await screen.findByRole("option", { name: "Bram" });

    await u.selectOptions(screen.getByRole("combobox", { name: "Persoon" }), "Bram");
    await u.selectOptions(screen.getByRole("combobox", { name: "Gebeurtenis" }), "Rol gewijzigd");
    await waitFor(() => {
      const last = seen.at(-1);
      expect(last?.get("user_id")).toBe(bram.user_id);
      expect(last?.get("code")).toBe("member_role_changed");
    });
    await screen.findAllByRole("row", { name: /Rol gewijzigd/ });
    const lastRow = first.at(-1) as Event;

    await u.click(await screen.findByRole("button", { name: "Meer laden" }));
    await waitFor(() => expect(seen.at(-1)?.get("before")).toBe(lastRow.created_at));
    expect(seen.at(-1)?.get("user_id")).toBe(bram.user_id);
    expect(seen.at(-1)?.get("code")).toBe("member_role_changed");
    // The short second page is the end: no more button.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Meer laden" })).not.toBeInTheDocument(),
    );
    expect(screen.getAllByRole("row", { name: /Rol gewijzigd/ })).toHaveLength(101);
  });

  it("sends the dates as since and until", async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`${base}/audit-events`, ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json([event(0)]);
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await screen.findByRole("row", { name: /Rol gewijzigd/ });
    await u.type(screen.getByLabelText("Van"), "2026-11-01");
    await u.type(screen.getByLabelText("Tot"), "2026-11-17");
    await waitFor(() => {
      expect(seen.at(-1)?.get("since")).toBe("2026-11-01");
      expect(seen.at(-1)?.get("until")).toBe("2026-11-17");
    });
  });

  it("the csv link carries the same filters", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`${base}/audit-events`, () => HttpResponse.json([event(0)])),
    );
    const u = userEvent.setup();
    renderApp(path);
    const link = await screen.findByRole("link", { name: "Download als CSV" });
    expect(link).toHaveAttribute("href", `/api/v1/workspaces/${workspace.id}/audit-events/csv`);
    await screen.findByRole("option", { name: "Bram" });
    await u.selectOptions(screen.getByRole("combobox", { name: "Persoon" }), "Bram");
    await u.selectOptions(screen.getByRole("combobox", { name: "Gebeurtenis" }), "Rol gewijzigd");
    await u.type(screen.getByLabelText("Van"), "2026-11-01");
    await waitFor(() => {
      const href =
        screen.getByRole("link", { name: "Download als CSV" }).getAttribute("href") ?? "";
      const params = new URL(href, "http://localhost").searchParams;
      expect(params.get("user_id")).toBe(bram.user_id);
      expect(params.get("code")).toBe("member_role_changed");
      expect(params.get("since")).toBe("2026-11-01");
      expect(params.has("before")).toBe(false);
    });
  });

  it("offers only workspace-level events in the filter", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`${base}/audit-events`, () => HttpResponse.json([event(0)])),
    );
    renderApp(path);
    const select = await screen.findByRole("combobox", { name: "Gebeurtenis" });
    const options = within(select)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toContain("Rol gewijzigd");
    expect(options).not.toContain("Inloggen mislukt");
    expect(options).not.toContain("Serverbeheerder gemaakt");
  });

  it("says what to do when nothing matches", async () => {
    server.use(
      ...loggedInHandlers(),
      ...memberHandlers(),
      http.get(`${base}/audit-events`, () => HttpResponse.json([])),
    );
    renderApp(path);
    expect(await screen.findByText("Geen gebeurtenissen gevonden.")).toBeInTheDocument();
  });

  it("a member is sent away from the log", async () => {
    server.use(
      asRole("member"),
      ...loggedInHandlers(),
      http.get(`${base}/documents`, () => HttpResponse.json([])),
      http.get(`${base}/persons`, () => HttpResponse.json([])),
      http.get(`${base}/labels`, () => HttpResponse.json([])),
      http.get(`${base}/ai`, () => HttpResponse.json({ mode: "off", has_api_key: false })),
    );
    renderApp(path);
    await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(screen.queryByRole("link", { name: "Download als CSV" })).not.toBeInTheDocument();
  });
});
