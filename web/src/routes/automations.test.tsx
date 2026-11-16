import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  apkRule,
  bankRule,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  ruleHandlers,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/automatisering`;
const base = `*${API}/workspaces/${workspace.id}`;

function asMember() {
  return http.get(`*${API}/workspaces`, () =>
    HttpResponse.json([{ ...workspace, role: "member" }]),
  );
}

describe("automations", () => {
  it("lists the rules with sentence, counters and a link to the editor", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers());
    renderApp(path);
    const link = await screen.findByRole("link", { name: "APK naar Auto" });
    expect(link).toHaveAttribute("href", expect.stringContaining(`/automatisering/${apkRule.id}`));
    expect(screen.getByText("Als de tekst het woord APK bevat → label Sales")).toBeVisible();
    expect(screen.getByText("12 keer toegepast, 3 keer voorgesteld")).toBeVisible();
    expect(screen.getByRole("link", { name: "Automatisering" })).toBeInTheDocument();
  });

  it("sends the whole rule with the changed mode when 'Automatisch toepassen' is switched", async () => {
    const puts: unknown[] = [];
    server.use(
      http.put(`${base}/rules/${apkRule.id}`, async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json({ ...apkRule, mode: "auto" });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(
      await screen.findByRole("switch", { name: "Automatisch toepassen: APK naar Auto" }),
    );
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({
      name: "APK naar Auto",
      active: true,
      mode: "auto",
      stop: false,
      match: "all",
      conditions: apkRule.conditions,
      actions: apkRule.actions,
    });
  });

  it("switches a rule on and off with 'Actief'", async () => {
    const puts: { active: boolean }[] = [];
    server.use(
      http.put(`${base}/rules/${bankRule.id}`, async ({ request }) => {
        puts.push((await request.json()) as { active: boolean });
        return HttpResponse.json({ ...bankRule, active: true });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    const active = await screen.findByRole("switch", { name: "Actief: Bank" });
    expect(active).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Automatisch toepassen: Bank" })).toBeChecked();
    await user.click(active);
    await waitFor(() => expect(puts).toEqual([expect.objectContaining({ active: true })]));
  });

  it("moves a rule down by sending the swapped order", async () => {
    const orders: unknown[] = [];
    server.use(
      http.put(`${base}/rules/order`, async ({ request }) => {
        orders.push(await request.json());
        return HttpResponse.json([bankRule, apkRule]);
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    expect(await screen.findByRole("button", { name: "Omhoog: APK naar Auto" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Omlaag: APK naar Auto" }));
    await waitFor(() => expect(orders).toEqual([{ rule_ids: [bankRule.id, apkRule.id] }]));
  });

  it("deletes a rule after a confirmation", async () => {
    const deleted: string[] = [];
    server.use(
      http.delete(`${base}/rules/${bankRule.id}`, () => {
        deleted.push(bankRule.id);
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Verwijderen: Bank" }));
    expect(deleted).toEqual([]);
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    await waitFor(() => expect(deleted).toEqual([bankRule.id]));
  });

  it("offers the start set and a new rule when there are no rules", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers([]));
    renderApp(path);
    expect(await screen.findByRole("link", { name: "Startset toevoegen" })).toHaveAttribute(
      "href",
      expect.stringContaining("/labels"),
    );
    expect(screen.getAllByRole("link", { name: "Nieuwe regel" }).length).toBeGreaterThan(0);
  });

  it("shows a member the rules without editing controls", async () => {
    server.use(asMember(), ...loggedInHandlers(), ...ruleHandlers());
    renderApp(path);
    expect(await screen.findByText("APK naar Auto")).toBeVisible();
    expect(screen.getByText("Alleen een beheerder kan regels wijzigen")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Nieuwe regel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Omlaag|Omhoog|Verwijderen/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "APK naar Auto" })).not.toBeInTheDocument();
  });
});
