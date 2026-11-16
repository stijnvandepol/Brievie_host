import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
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
    expect(await screen.findByText("1 van de nieuwste 500 brieven passen")).toBeVisible();
    expect(screen.getByText("Je hebt 600 brieven")).toBeVisible();
    expect(screen.getByRole("link", { name: "Belastingdienst" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/post/${documentSummary.id}`),
    );
    expect(tested).toEqual([expect.objectContaining({ name: "APK" })]);
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
    expect(
      screen.getByText("De regel wordt op al je post toegepast, niet alleen op de nieuwste 500."),
    ).toBeVisible();
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
    expect(await screen.findByText("Alleen een beheerder kan regels wijzigen")).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Naam" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Opslaan" })).not.toBeInTheDocument();
  });
});
