import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  API,
  annaPerson,
  apkRule,
  documentSummary,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  renderApp,
  ruleHandlers,
  sales,
  server,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;
const newPath = `/w/${workspace.id}/automatisering/nieuw`;

function condition(n: number) {
  return screen.getByRole("group", { name: `Voorwaarde ${n}` });
}

function action(n: number) {
  return screen.getByRole("group", { name: `Actie ${n}` });
}

describe("rule editor", () => {
  it("builds a rule from the controls and posts it", async () => {
    const posts: unknown[] = [];
    server.use(
      http.post(`${base}/rules`, async ({ request }) => {
        posts.push(await request.json());
        return HttpResponse.json(
          { ...apkRule, id: "f3333333-3333-4333-8333-333333333333" },
          { status: 201 },
        );
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK naar Auto");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.selectOptions(within(action(1)).getByRole("combobox", { name: "Label" }), "Sales");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      name: "APK naar Auto",
      active: true,
      mode: "propose",
      stop: false,
      match: "all",
      conditions: [{ field: "text", op: "contains_word", value: "APK" }],
      actions: [{ type: "add_label", label_id: sales.id }],
    });
    // The save opens the saved rule: wait for that page, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: "Regel wijzigen" })).toBeVisible();
  });

  it("explains how the switch works together with the AI's autopilot", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers([]));
    renderApp(newPath);
    const mode = await screen.findByRole("switch", { name: "Automatisch toepassen" });
    expect(mode).toHaveAccessibleDescription(
      "Aan: de regel zet het label of de naam er altijd zelf op. Uit: de regel doet een voorstel dat jij bevestigt. Staat bij AI 'Brieven zelf onder personen en labels zetten' aan? Dan zet Brievie een voorgesteld label er toch zelf op als de regel het woord in de afzender of bovenaan de brief vond, of als de AI zeker is van hetzelfde label. Staat het woord alleen verderop en is de AI niet zeker? Dan blijft het een voorstel.",
    );
  });

  it("resets the operator and value when the field changes", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers([]));
    const user = userEvent.setup();
    renderApp(newPath);
    await screen.findByRole("textbox", { name: "Naam" });
    const row = condition(1);
    await user.type(within(row).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.selectOptions(
      within(row).getByRole("combobox", { name: "Vergelijking" }),
      "bevat de tekst",
    );
    await user.selectOptions(within(row).getByRole("combobox", { name: "Veld" }), "Op naam van");
    const operators = within(within(row).getByRole("combobox", { name: "Vergelijking" }))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(operators).toEqual(["is", "is leeg"]);
    expect(within(row).getByRole("combobox", { name: "Vergelijking" })).toHaveValue("is");
    expect(within(row).getByRole("combobox", { name: "Waarde" })).toHaveValue(papa.id);
    await user.selectOptions(
      within(row).getByRole("combobox", { name: "Vergelijking" }),
      "is leeg",
    );
    expect(within(row).queryByRole("combobox", { name: "Waarde" })).not.toBeInTheDocument();
    await user.selectOptions(within(row).getByRole("combobox", { name: "Veld" }), "De bron");
    expect(within(row).getByRole("combobox", { name: "Waarde" })).toHaveValue("scan");
    await user.selectOptions(within(row).getByRole("combobox", { name: "Veld" }), "De tekst");
    expect(within(row).getByRole("textbox", { name: "Waarde" })).toHaveValue("");
  });

  it("adds and removes condition and action rows", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers([]));
    const user = userEvent.setup();
    renderApp(newPath);
    await screen.findByRole("textbox", { name: "Naam" });
    await user.click(screen.getByRole("button", { name: "Voorwaarde toevoegen" }));
    await user.click(screen.getByRole("button", { name: "Actie toevoegen" }));
    expect(condition(2)).toBeInTheDocument();
    expect(action(2)).toBeInTheDocument();
    await user.selectOptions(
      within(action(2)).getByRole("combobox", { name: "Actie" }),
      "Op naam zetten van",
    );
    expect(within(action(2)).getByRole("combobox", { name: "Persoon" })).toHaveValue(papa.id);
    await user.click(screen.getByRole("button", { name: "Voorwaarde 2 verwijderen" }));
    await user.click(screen.getByRole("button", { name: "Actie 2 verwijderen" }));
    expect(screen.queryByRole("group", { name: "Voorwaarde 2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Actie 2" })).not.toBeInTheDocument();
    // The last row of each cannot go.
    expect(screen.getByRole("button", { name: "Voorwaarde 1 verwijderen" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Actie 1 verwijderen" })).toBeDisabled();
  });

  it("tests the unsaved form and links the documents", async () => {
    const tested: unknown[] = [];
    server.use(
      http.post(`${base}/rules/test`, async ({ request }) => {
        tested.push(await request.json());
        return HttpResponse.json({
          total_count: 600,
          checked_count: 500,
          matched_count: 1,
          documents: [documentSummary],
        });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    expect(await screen.findByText("1 van de nieuwste 500 brieven past")).toBeVisible();
    expect(screen.getByText("Je hebt 600 brieven")).toBeVisible();
    expect(screen.getByRole("link", { name: "Belastingdienst" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/post/${documentSummary.id}`),
    );
    expect(tested).toEqual([expect.objectContaining({ name: "APK" })]);
  });

  it("reads singular for one matching letter and scrolls the result into view", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json({
          total_count: 5,
          checked_count: 5,
          matched_count: 1,
          documents: [documentSummary],
        }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    const result = await screen.findByRole("status", { name: "Uitkomst van de test" });
    expect(within(result).getByText("1 van de nieuwste 5 brieven past")).toBeVisible();
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: "nearest" }));
  });

  it("reads plural for several matching letters", async () => {
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json({ total_count: 5, checked_count: 5, matched_count: 3, documents: [] }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    expect(await screen.findByText("3 van de nieuwste 5 brieven passen")).toBeVisible();
  });

  it("mentions the limit of the test only when it did not look at every letter", async () => {
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json({
          total_count: 600,
          checked_count: 500,
          matched_count: 1,
          documents: [],
        }),
      ),
      http.put(`${base}/rules/${apkRule.id}`, () => HttpResponse.json(apkRule)),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(`/w/${workspace.id}/automatisering/${apkRule.id}`);
    await screen.findByRole("textbox", { name: "Naam" });
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    await screen.findByText("1 van de nieuwste 500 brieven past");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(
      await screen.findByText(
        "De regel wordt op al je post toegepast, niet alleen op de nieuwste 500.",
      ),
    ).toBeVisible();
  });

  it("announces the server's validation sentence", async () => {
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json(errorBody("invalid_rule", "Vul bij voorwaarde 1 minstens 2 tekens in."), {
          status: 422,
        }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Vul bij voorwaarde 1 minstens 2 tekens in.",
    );
  });

  it("loads a rule, saves it with PUT and then offers to run it on existing post", async () => {
    const puts: unknown[] = [];
    const runs: string[] = [];
    server.use(
      http.put(`${base}/rules/${apkRule.id}`, async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json(apkRule);
      }),
      http.post(`${base}/rules/${apkRule.id}/run`, () => {
        runs.push(apkRule.id);
        return HttpResponse.json({ queued: true }, { status: 202 });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(`/w/${workspace.id}/automatisering/${apkRule.id}`);
    expect(await screen.findByRole("textbox", { name: "Naam" })).toHaveValue("APK naar Auto");
    expect(within(condition(1)).getByRole("textbox", { name: "Waarde" })).toHaveValue("APK");
    expect(screen.queryByText("Toepassen op bestaande post?")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(await screen.findByText("Toepassen op bestaande post?")).toBeVisible();
    expect(screen.getByText("De regel wordt op al je post toegepast.")).toBeVisible();
    expect(screen.queryByText(/niet alleen op de nieuwste/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Toepassen" }));
    expect(
      await screen.findByText("Bezig. Je ziet het resultaat bij de brieven zelf."),
    ).toBeVisible();
    expect(runs).toEqual([apkRule.id]);
  });

  it("does not offer to run a rule that is switched off", async () => {
    server.use(
      http.put(`${base}/rules/${apkRule.id}`, () =>
        HttpResponse.json({ ...apkRule, active: false }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    const user = userEvent.setup();
    renderApp(`/w/${workspace.id}/automatisering/${apkRule.id}`);
    await user.click(await screen.findByRole("switch", { name: "Actief" }));
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByText("De regel is opgeslagen.")).toBeVisible();
    expect(screen.queryByText("Toepassen op bestaande post?")).not.toBeInTheDocument();
  });

  it("shows a member no editing controls", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "member" }])),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    renderApp(newPath);
    expect(await screen.findByText("Alleen een beheerder kan regels wijzigen.")).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Naam" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Opslaan" })).not.toBeInTheDocument();
  });

  it("shows a viewer no error from the rules it may not read", async () => {
    let fetched = false;
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      http.get(`${base}/rules`, () => {
        fetched = true;
        return HttpResponse.json(errorBody("forbidden", "Nee."), { status: 403 });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers(),
    );
    renderApp(`/w/${workspace.id}/automatisering/${apkRule.id}`);
    expect(await screen.findByText("Alleen een beheerder kan regels wijzigen.")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetched).toBe(false);
  });

  it("keeps the value when only the operator changes", async () => {
    server.use(...loggedInHandlers(), ...ruleHandlers([]));
    const user = userEvent.setup();
    renderApp(newPath);
    await screen.findByRole("textbox", { name: "Naam" });
    const row = condition(1);
    await user.type(within(row).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.selectOptions(
      within(row).getByRole("combobox", { name: "Vergelijking" }),
      "bevat de tekst",
    );
    expect(within(row).getByRole("textbox", { name: "Waarde" })).toHaveValue("APK");
    await user.selectOptions(within(row).getByRole("combobox", { name: "Veld" }), "Een label");
    await user.selectOptions(within(row).getByRole("combobox", { name: "Waarde" }), "Sales");
    await user.selectOptions(
      within(row).getByRole("combobox", { name: "Vergelijking" }),
      "heeft niet",
    );
    expect(within(row).getByRole("combobox", { name: "Waarde" })).toHaveValue(sales.id);
    await user.selectOptions(within(row).getByRole("combobox", { name: "Veld" }), "Op naam van");
    await user.selectOptions(
      within(row).getByRole("combobox", { name: "Vergelijking" }),
      "is leeg",
    );
    await user.selectOptions(within(row).getByRole("combobox", { name: "Vergelijking" }), "is");
    expect(within(row).getByRole("combobox", { name: "Waarde" })).toHaveValue(papa.id);
  });

  it("posts label and addressee conditions and an addressee action", async () => {
    const posts: unknown[] = [];
    server.use(
      http.post(`${base}/rules`, async ({ request }) => {
        posts.push(await request.json());
        return HttpResponse.json(apkRule, { status: 201 });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "Mix");
    await user.selectOptions(
      within(condition(1)).getByRole("combobox", { name: "Veld" }),
      "Een label",
    );
    await user.selectOptions(
      within(condition(1)).getByRole("combobox", { name: "Waarde" }),
      "Sales",
    );
    await user.click(screen.getByRole("button", { name: "Voorwaarde toevoegen" }));
    await user.selectOptions(
      within(condition(2)).getByRole("combobox", { name: "Veld" }),
      "Op naam van",
    );
    await user.selectOptions(
      within(condition(2)).getByRole("combobox", { name: "Vergelijking" }),
      "is leeg",
    );
    await user.selectOptions(
      within(action(1)).getByRole("combobox", { name: "Actie" }),
      "Op naam zetten van",
    );
    await user.selectOptions(within(action(1)).getByRole("combobox", { name: "Persoon" }), "Papa");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual(
      expect.objectContaining({
        conditions: [
          { field: "label", op: "has", value: sales.id },
          { field: "addressee", op: "is_empty", value: "" },
        ],
        actions: [{ type: "add_addressee", person_id: papa.id }],
      }),
    );
    // The save opens the saved rule: wait for that page, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: "Regel wijzigen" })).toBeVisible();
  });

  it("clears the test result when the form is edited", async () => {
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json({ total_count: 1, checked_count: 1, matched_count: 0, documents: [] }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    expect(await screen.findByText("0 van de nieuwste 1 brieven passen")).toBeVisible();
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "A");
    expect(screen.queryByText("0 van de nieuwste 1 brieven passen")).not.toBeInTheDocument();
  });

  it("disables what cannot be chosen when there are no labels or persons", async () => {
    server.use(
      http.get(`${base}/labels`, () => HttpResponse.json([])),
      http.get(`${base}/persons`, () => HttpResponse.json([])),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    renderApp(newPath);
    await screen.findByRole("textbox", { name: "Naam" });
    expect(screen.getByText("Maak eerst een label.")).toBeVisible();
    expect(screen.getByText("Voeg eerst een persoon toe.")).toBeVisible();
    expect(within(condition(1)).getByRole("option", { name: "Een label" })).toBeDisabled();
    expect(within(condition(1)).getByRole("option", { name: "Op naam van" })).toBeDisabled();
    expect(within(action(1)).getByRole("option", { name: "Label toevoegen" })).toBeDisabled();
    expect(within(action(1)).getByRole("option", { name: "Op naam zetten van" })).toBeDisabled();
  });

  it("shows a deleted label and an archived person as what is saved", async () => {
    const gone = "e9999999-9999-4999-8999-999999999999";
    const rule = {
      ...apkRule,
      conditions: [{ field: "label", op: "has", value: gone }],
      actions: [{ type: "add_addressee", person_id: papa.id }],
    };
    server.use(
      http.get(`${base}/persons`, () => HttpResponse.json([{ ...papa, archived: true }])),
      ...loggedInHandlers(),
      ...ruleHandlers([rule]),
    );
    renderApp(`/w/${workspace.id}/automatisering/${apkRule.id}`);
    await screen.findByRole("textbox", { name: "Naam" });
    expect(within(condition(1)).getByRole("combobox", { name: "Waarde" })).toHaveValue(gone);
    expect(within(condition(1)).getByRole("option", { name: "(verwijderd)" })).toBeInTheDocument();
    expect(within(action(1)).getByRole("combobox", { name: "Persoon" })).toHaveValue(papa.id);
    expect(
      within(action(1)).getByRole("option", { name: "Papa (gearchiveerd)" }),
    ).toBeInTheDocument();
  });

  it("starts on a person action when there are no labels, and posts person_id", async () => {
    const posts: unknown[] = [];
    server.use(
      http.get(`${base}/labels`, () => HttpResponse.json([])),
      http.post(`${base}/rules`, async ({ request }) => {
        posts.push(await request.json());
        return HttpResponse.json(apkRule, { status: 201 });
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "Papa");
    expect(within(action(1)).getByRole("combobox", { name: "Actie" })).toHaveValue("add_addressee");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.click(screen.getByRole("button", { name: "Actie toevoegen" }));
    expect(within(action(2)).getByRole("combobox", { name: "Actie" })).toHaveValue("add_addressee");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual(
      expect.objectContaining({
        actions: [
          { type: "add_addressee", person_id: papa.id },
          { type: "add_addressee", person_id: papa.id },
        ],
      }),
    );
    // The save opens the saved rule: wait for that page, so no navigation outlives the test.
    expect(await screen.findByRole("heading", { level: 1, name: "Regel wijzigen" })).toBeVisible();
  });

  it("sends nothing when there are no labels and no persons", async () => {
    const requests: string[] = [];
    server.use(
      http.get(`${base}/labels`, () => HttpResponse.json([])),
      http.get(`${base}/persons`, () => HttpResponse.json([])),
      http.post(`${base}/rules`, () => {
        requests.push("save");
        return HttpResponse.json(apkRule, { status: 201 });
      }),
      http.post(`${base}/rules/test`, () => {
        requests.push("test");
        return HttpResponse.json({});
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "X");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Maak eerst een label of voeg een persoon toe.",
    );
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    expect(requests).toEqual([]);
  });

  it("sends nothing when a row still has no label or person", async () => {
    const requests: string[] = [];
    const rule = { ...apkRule, actions: [{ type: "add_label", label_id: null }] };
    server.use(
      http.put(`${base}/rules/${apkRule.id}`, () => {
        requests.push("save");
        return HttpResponse.json(apkRule);
      }),
      ...loggedInHandlers(),
      ...ruleHandlers([rule]),
    );
    const user = userEvent.setup();
    renderApp(`/w/${workspace.id}/automatisering/${apkRule.id}`);
    await user.click(await screen.findByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Kies bij elke voorwaarde en actie een label of persoon.",
    );
    expect(requests).toEqual([]);
  });

  it("goes to the saved rule's own page after the first save, so a second save is a PUT", async () => {
    const newId = "f3333333-3333-4333-8333-333333333333";
    const posts: unknown[] = [];
    const puts: unknown[] = [];
    let created = false;
    server.use(
      http.post(`${base}/rules`, async ({ request }) => {
        posts.push(await request.json());
        created = true;
        return HttpResponse.json({ ...apkRule, id: newId }, { status: 201 });
      }),
      http.put(`${base}/rules/${newId}`, async ({ request }) => {
        puts.push(await request.json());
        return HttpResponse.json({ ...apkRule, id: newId });
      }),
      http.get(`${base}/rules`, () =>
        HttpResponse.json(created ? [{ ...apkRule, id: newId }] : []),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    const { router } = renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK naar Auto");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.selectOptions(within(action(1)).getByRole("combobox", { name: "Label" }), "Sales");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`/w/${workspace.id}/automatisering/${newId}`),
    );
    expect(await screen.findByRole("heading", { name: "Regel wijzigen" })).toBeVisible();
    expect(await screen.findByText("Toepassen op bestaande post?")).toBeVisible();
    // The flag is consumed: a reload would not show the offer again.
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(screen.getByText("Toepassen op bestaande post?")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(posts).toHaveLength(1);
  });

  it("names archived persons and says so when no letter matches", async () => {
    const archived = { ...papa, archived: true };
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json({
          total_count: 1,
          checked_count: 1,
          matched_count: 1,
          documents: [{ ...documentSummary, addressee_ids: [papa.id] }],
        }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
      http.get(`${base}/persons`, () => HttpResponse.json([archived, annaPerson])),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(within(link.closest("tr") as HTMLElement).getByText("Papa")).toBeVisible();
  });

  it("says no letters match when the test finds none", async () => {
    server.use(
      http.post(`${base}/rules/test`, () =>
        HttpResponse.json({ total_count: 3, checked_count: 3, matched_count: 0, documents: [] }),
      ),
      ...loggedInHandlers(),
      ...ruleHandlers([]),
    );
    const user = userEvent.setup();
    renderApp(newPath);
    await user.type(await screen.findByRole("textbox", { name: "Naam" }), "APK");
    await user.type(within(condition(1)).getByRole("textbox", { name: "Waarde" }), "APK");
    await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
    expect(await screen.findByText("Geen brieven passen.")).toBeVisible();
  });

  describe("from an example", () => {
    const senderPath = `${newPath}?voorbeeld=label_by_sender`;

    it("opens the editor filled in, with the blanks marked and nothing saved", async () => {
      let posts = 0;
      server.use(
        http.post(`${base}/rules`, () => {
          posts += 1;
          return HttpResponse.json(apkRule, { status: 201 });
        }),
        ...loggedInHandlers(),
        ...ruleHandlers([]),
      );
      renderApp(senderPath);
      expect(await screen.findByRole("textbox", { name: "Naam" })).toHaveValue("Label op afzender");
      const intro = screen.getByRole("region", { name: "Voorbeeld: Label op afzender" });
      expect(within(intro).getByText("Preciezer dan een woord in de brief.")).toBeVisible();
      const row = condition(1);
      expect(within(row).getByRole("combobox", { name: "Veld" })).toHaveValue("sender");
      const value = within(row).getByRole("textbox", { name: "Waarde" });
      expect(value).toHaveValue("");
      expect(value).toHaveAttribute("placeholder", "bijv. Zilveren Kruis");
      expect(value).toHaveAccessibleDescription(
        "Typ de naam van de afzender, zoals die op de brief staat.",
      );
      expect(within(row).getByText("Vul in")).toBeVisible();
      // No label is chosen for the user.
      const label = within(action(1)).getByRole("combobox", { name: "Label" });
      expect(label).toHaveValue("");
      expect(within(action(1)).getByText("Kies")).toBeVisible();
      expect(posts).toBe(0);
    });

    it("tries the filled-in example on existing post before anything is saved", async () => {
      const tested: unknown[] = [];
      let posts = 0;
      server.use(
        http.post(`${base}/rules/test`, async ({ request }) => {
          tested.push(await request.json());
          return HttpResponse.json({
            total_count: 5,
            checked_count: 5,
            matched_count: 1,
            documents: [documentSummary],
          });
        }),
        http.post(`${base}/rules`, () => {
          posts += 1;
          return HttpResponse.json(apkRule, { status: 201 });
        }),
        ...loggedInHandlers(),
        ...ruleHandlers([]),
      );
      const user = userEvent.setup();
      renderApp(senderPath);
      const value = await screen.findByRole("textbox", { name: "Waarde" });
      await user.type(value, "Zilveren Kruis");
      expect(within(condition(1)).queryByText("Vul in")).not.toBeInTheDocument();
      await user.selectOptions(within(action(1)).getByRole("combobox", { name: "Label" }), "Sales");
      await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
      expect(await screen.findByText("1 van de nieuwste 5 brieven past")).toBeVisible();
      expect(screen.getByText("Er is nog niets opgeslagen.")).toBeVisible();
      expect(tested).toEqual([
        {
          name: "Label op afzender",
          active: true,
          mode: "propose",
          stop: false,
          match: "all",
          conditions: [{ field: "sender", op: "contains_word", value: "Zilveren Kruis" }],
          actions: [{ type: "add_label", label_id: sales.id }],
        },
      ]);
      expect(posts).toBe(0);
    });

    it("asks for a label instead of trying the example without one", async () => {
      let tests = 0;
      server.use(
        http.post(`${base}/rules/test`, () => {
          tests += 1;
          return HttpResponse.json({
            total_count: 0,
            checked_count: 0,
            matched_count: 0,
            documents: [],
          });
        }),
        ...loggedInHandlers(),
        ...ruleHandlers([]),
      );
      const user = userEvent.setup();
      renderApp(senderPath);
      await user.type(await screen.findByRole("textbox", { name: "Waarde" }), "Menzis");
      await user.click(screen.getByRole("button", { name: "Test op bestaande post" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Kies bij elke voorwaarde en actie een label of persoon.",
      );
      expect(tests).toBe(0);
    });

    it("makes the suggested label only when asked, then picks it", async () => {
      const wonen = { ...sales, id: "d3333333-3333-4333-8333-333333333333", name: "Wonen" };
      const created: unknown[] = [];
      let labels = [sales];
      server.use(
        http.post(`${base}/labels`, async ({ request }) => {
          created.push(await request.json());
          labels = [sales, wonen];
          return HttpResponse.json(wonen, { status: 201 });
        }),
        http.get(`${base}/labels`, () => HttpResponse.json(labels)),
        ...loggedInHandlers(),
        ...ruleHandlers([]),
      );
      const user = userEvent.setup();
      renderApp(`${newPath}?voorbeeld=municipality_woz`);
      expect(await screen.findByRole("textbox", { name: "Naam" })).toHaveValue(
        "WOZ van de gemeente",
      );
      expect(within(condition(1)).getByRole("textbox", { name: "Waarde" })).toHaveValue("gemeente");
      expect(within(condition(2)).getByRole("textbox", { name: "Waarde" })).toHaveValue("WOZ");
      const label = within(action(1)).getByRole("combobox", { name: "Label" });
      expect(label).toHaveValue("");
      expect(created).toEqual([]);
      await user.click(within(action(1)).getByRole("button", { name: "Label Wonen maken" }));
      await waitFor(() => expect(label).toHaveValue(wonen.id));
      expect(within(label).getByRole("option", { name: "Wonen" })).toHaveProperty("selected", true);
      expect(created).toEqual([{ name: "Wonen", color: "grey" }]);
      expect(
        within(action(1)).queryByRole("button", { name: "Label Wonen maken" }),
      ).not.toBeInTheDocument();
    });

    it("picks the suggested start-set label when the workspace has it", async () => {
      const wonen = {
        ...sales,
        id: "d3333333-3333-4333-8333-333333333333",
        name: "Thuis en huis",
        system_key: "wonen",
      };
      server.use(
        http.get(`${base}/labels`, () => HttpResponse.json([sales, wonen])),
        ...loggedInHandlers(),
        ...ruleHandlers([]),
      );
      renderApp(`${newPath}?voorbeeld=municipality_woz`);
      await screen.findByRole("textbox", { name: "Naam" });
      expect(within(action(1)).getByRole("combobox", { name: "Label" })).toHaveValue(wonen.id);
      expect(within(action(1)).queryByRole("button", { name: /maken/ })).not.toBeInTheDocument();
    });

    it("says so when the example does not exist, and offers an empty rule", async () => {
      server.use(...loggedInHandlers(), ...ruleHandlers([]));
      renderApp(`${newPath}?voorbeeld=bestaat_niet`);
      expect(await screen.findByText("Dit voorbeeld bestaat niet (meer).")).toBeVisible();
      expect(screen.getByRole("textbox", { name: "Naam" })).toHaveValue("");
    });
  });
});
