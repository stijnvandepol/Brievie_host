import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  apkRule,
  bankRule,
  gate,
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

  it("applies the start set from the empty state and fetches rules and labels again", async () => {
    let presets = 0;
    let ruleFetches = 0;
    let labelFetches = 0;
    server.use(
      http.post(`${base}/labels/preset`, () => {
        presets += 1;
        return HttpResponse.json([]);
      }),
      http.get(`${base}/rules`, () => {
        ruleFetches += 1;
        return HttpResponse.json([]);
      }),
      http.get(`${base}/labels`, () => {
        labelFetches += 1;
        return HttpResponse.json([]);
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Startset toevoegen" }));
    await waitFor(() => expect(presets).toBe(1));
    await waitFor(() => expect(ruleFetches).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(labelFetches).toBeGreaterThanOrEqual(2));
    expect(screen.getAllByRole("link", { name: "Nieuwe regel" }).length).toBeGreaterThan(0);
  });

  it("keeps focus on the pressed move button and ignores a second press while pending", async () => {
    let orders = 0;
    let order = [apkRule, bankRule];
    const answer = gate();
    server.use(
      http.put(`${base}/rules/order`, async () => {
        orders += 1;
        await answer.opened;
        order = [bankRule, apkRule];
        return HttpResponse.json(order);
      }),
      http.get(`${base}/rules`, () => HttpResponse.json(order)),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    const down = await screen.findByRole("button", { name: "Omlaag: APK naar Auto" });
    await user.click(down);
    expect(down).toHaveFocus();
    expect(down).toBeEnabled();
    // The move is on its way and stays pending until the server answers.
    await waitFor(() => expect(orders).toBe(1));
    await user.keyboard("{Enter}");
    answer.open();
    // The APK rule is last now: its "Omlaag" is disabled, so focus is on its "Omhoog".
    const up = await screen.findByRole("button", { name: "Omhoog: APK naar Auto" });
    await waitFor(() => expect(up).toHaveFocus());
    expect(orders).toBe(1);
  });

  it("keeps focus on the same move button while the rule stays in the middle", async () => {
    const third = { ...bankRule, id: "f3333333-3333-4333-8333-333333333333", name: "Derde" };
    let order = [apkRule, bankRule, third];
    server.use(
      http.put(`${base}/rules/order`, async () => {
        order = [bankRule, apkRule, third];
        return HttpResponse.json(order);
      }),
      http.get(`${base}/rules`, () => HttpResponse.json(order)),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Omlaag: APK naar Auto" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Omlaag: APK naar Auto" })).toHaveFocus(),
    );
    expect(screen.getAllByRole("button", { name: /^Omlaag: / })[1]).toHaveAccessibleName(
      "Omlaag: APK naar Auto",
    );
  });

  it("shows a member the rules without editing controls", async () => {
    server.use(asMember(), ...loggedInHandlers(), ...ruleHandlers());
    renderApp(path);
    expect(await screen.findByText("APK naar Auto")).toBeVisible();
    expect(screen.getByText("Alleen een beheerder kan regels wijzigen.")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Nieuwe regel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Omlaag|Omhoog|Verwijderen/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "APK naar Auto" })).not.toBeInTheDocument();
  });

  it("does not offer or fetch the rules for a viewer", async () => {
    let fetched = false;
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      http.get(`${base}/rules`, () => {
        fetched = true;
        return HttpResponse.json({ code: "forbidden", message: "Nee." }, { status: 403 });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    renderApp(path);
    expect(
      await screen.findByText("Alleen leden en beheerders van deze werkruimte zien de regels."),
    ).toBeVisible();
    expect(screen.queryByRole("link", { name: "Automatisering" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetched).toBe(false);
  });

  it("disables a rule's switches while its update is pending", async () => {
    const answer = gate();
    server.use(
      http.put(`${base}/rules/${apkRule.id}`, async () => {
        await answer.opened;
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
    expect(screen.getByRole("switch", { name: "Actief: APK naar Auto" })).toBeDisabled();
    answer.open();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Actief: APK naar Auto" })).toBeEnabled(),
    );
  });

  it("shows the examples instead of an empty list, the workspace's kind first", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers([]));
    renderApp(path);
    const gallery = await screen.findByRole("region", { name: "Begin met een voorbeeld" });
    const titles = within(gallery)
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    // A home workspace: the business-only example comes last.
    expect(titles).toEqual([
      "Label op afzender",
      "De WOZ-beschikking van de gemeente",
      "Facturen van een leverancier",
    ]);
    expect(within(gallery).getByText("Thuis en organisatie")).toBeVisible();
    expect(within(gallery).getByText("Organisatie")).toBeVisible();
    expect(
      within(gallery).getByText("Stelt een label voor op alle post van één afzender."),
    ).toBeVisible();
    const use = within(gallery).getByRole("link", {
      name: "Gebruik dit voorbeeld: Label op afzender",
    });
    expect(use).toHaveAttribute(
      "href",
      expect.stringContaining("/automatisering/nieuw?voorbeeld=label_by_sender"),
    );
    const how = screen.getByRole("region", { name: "Zo werkt een regel" });
    expect(within(how).getByText("Wanneer")).toBeVisible();
    expect(within(how).getByText("Als")).toBeVisible();
    expect(within(how).getByText("Dan")).toBeVisible();
  });

  it("keeps the examples below the rules once there are rules", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers());
    renderApp(path);
    expect(await screen.findByRole("link", { name: "APK naar Auto" })).toBeVisible();
    expect(
      await screen.findByRole("link", { name: "Gebruik dit voorbeeld: Label op afzender" }),
    ).toBeVisible();
  });

  it("explains rules to a member but offers no examples", async () => {
    let asked = false;
    server.use(
      asMember(),
      http.get(`${base}/rules/templates`, () => {
        asked = true;
        return HttpResponse.json([]);
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    renderApp(path);
    expect(await screen.findByRole("region", { name: "Zo werkt een regel" })).toBeVisible();
    expect(
      screen.queryByRole("region", { name: "Begin met een voorbeeld" }),
    ).not.toBeInTheDocument();
    expect(asked).toBe(false);
  });

  it("offers the start set to an admin only", async () => {
    server.use(asMember(), ...loggedInHandlers(), ...ruleHandlers([]));
    renderApp(path);
    expect(await screen.findByText("Je hebt nog geen regels.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Startset toevoegen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Startset toevoegen" })).not.toBeInTheDocument();
  });
});
