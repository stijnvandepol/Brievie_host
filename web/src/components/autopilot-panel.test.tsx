import { screen, waitFor, within } from "@testing-library/react";
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

const path = `/w/${workspace.id}/ai`;
const base = `*${API}/workspaces/${workspace.id}`;
const NOTICE =
  "Niemand controleert deze brieven.\n\nFouten zijn mogelijk.\nDe termijnen staan in de brief.";

type Body = Record<string, unknown>;

/** A server with AI in `mode` and the autopilot in `initial`; it records every PUT. */
function autopilotServer(mode: string, initial: Body = {}) {
  const state = {
    current: {
      allowed: true,
      auto_organise: false,
      auto_confirm: false,
      notice: NOTICE,
      ...initial,
    },
    puts: [] as Body[],
  };
  server.use(
    ...loggedInHandlers(),
    http.get(`${base}/ai`, () =>
      HttpResponse.json({ mode, has_api_key: false, autopilot_allowed: state.current.allowed }),
    ),
    http.get(`${base}/ai/suppliers`, () => HttpResponse.json([])),
    http.get(`${base}/ai/autopilot`, () => HttpResponse.json(state.current)),
    http.put(`${base}/ai/autopilot`, async ({ request }) => {
      const body = (await request.json()) as Body;
      state.puts.push(body);
      state.current = {
        ...state.current,
        auto_organise: Boolean(body.auto_organise),
        auto_confirm: Boolean(body.auto_confirm),
      };
      return HttpResponse.json(state.current);
    }),
  );
  return state;
}

describe("automatisch verwerken", () => {
  it("is not shown while AI is off", async () => {
    autopilotServer("off");
    renderApp(path);
    await screen.findByRole("heading", { name: "AI", level: 1 });
    expect(
      screen.queryByRole("heading", { name: "Automatisch verwerken" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("says the server's administrator switched it off, with no switches", async () => {
    autopilotServer("external", { allowed: false });
    renderApp(path);
    expect(
      await screen.findByText(
        "De beheerder van de server heeft automatisch verwerken uitgezet. Hier kun je het niet aanzetten.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("shows both switches with a label and a description, in their state", async () => {
    autopilotServer("external", { auto_organise: true });
    renderApp(path);
    const organise = await screen.findByRole("switch", {
      name: "Brieven zelf onder personen en labels zetten",
    });
    const confirm = screen.getByRole("switch", { name: "Brieven zelf bevestigen" });
    expect(organise).toBeChecked();
    expect(confirm).not.toBeChecked();
    expect(organise).toHaveAccessibleDescription(/\.$/);
    expect(confirm).toHaveAccessibleDescription(/\.$/);
  });

  it("says when a rule's proposed label is put on by itself", async () => {
    autopilotServer("external");
    renderApp(path);
    const organise = await screen.findByRole("switch", {
      name: "Brieven zelf onder personen en labels zetten",
    });
    expect(organise).toHaveAccessibleDescription(
      "Brievie zet een brief zelf bij de juiste persoon en het juiste label als de AI of de naam duidelijk is. Een label dat een regel voorstelt, zet Brievie er ook op als de regel het woord in de afzender of bovenaan de brief vond, of als de AI hetzelfde label voorstelt. Een regel die op 'Automatisch toepassen' staat, doet dat altijd.",
    );
  });

  it("shows the notice as paragraphs and sends nothing until 'Aanzetten'", async () => {
    const state = autopilotServer("external");
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("switch", { name: "Brieven zelf bevestigen" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Niemand controleert deze brieven.")).toBeVisible();
    expect(
      within(dialog).getByText(/Fouten zijn mogelijk\.\s*De termijnen staan in de brief\./),
    ).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Annuleren" })).toHaveFocus();
    expect(state.puts).toEqual([]);

    await user.click(within(dialog).getByRole("button", { name: "Aanzetten" }));
    await within(document.body).findByRole("switch", {
      name: "Brieven zelf bevestigen",
      checked: true,
    });
    expect(state.puts).toEqual([{ auto_organise: false, auto_confirm: true, acknowledged: true }]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("changes nothing on 'Annuleren'", async () => {
    const state = autopilotServer("external");
    const user = userEvent.setup();
    renderApp(path);
    const organise = await screen.findByRole("switch", {
      name: "Brieven zelf onder personen en labels zetten",
    });
    await user.click(organise);
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Annuleren" }),
    );

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(state.puts).toEqual([]);
    expect(organise).not.toBeChecked();
  });

  it("switches off at once, without a question", async () => {
    const state = autopilotServer("external", { auto_organise: true, auto_confirm: true });
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("switch", { name: "Brieven zelf bevestigen" }));

    await screen.findByRole("switch", { name: "Brieven zelf bevestigen", checked: false });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(state.puts).toEqual([{ auto_organise: true, auto_confirm: false, acknowledged: false }]);
  });

  it("shows the server's Dutch message when it refuses", async () => {
    autopilotServer("external");
    server.use(
      http.put(`${base}/ai/autopilot`, () =>
        HttpResponse.json(
          errorBody("ai_autopilot_forbidden", "De serverbeheerder heeft dit uitgezet."),
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("switch", { name: "Brieven zelf bevestigen" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Aanzetten" }),
    );
    expect(await within(screen.getByRole("alertdialog")).findByRole("alert")).toHaveTextContent(
      "De serverbeheerder heeft dit uitgezet.",
    );
  });

  it.each([
    [{ auto_organise: true }, ["Brievie zet brieven zelf onder personen en labels."]],
    [{ auto_confirm: true }, ["Brievie bevestigt brieven zelf."]],
    [
      { auto_organise: true, auto_confirm: true },
      ["Brievie zet brieven zelf onder personen en labels.", "Brievie bevestigt brieven zelf."],
    ],
  ])("tells a member what is on (%o), with nothing to switch", async (initial, lines) => {
    autopilotServer("external", initial);
    server.use(asRole("member"));
    renderApp(path);
    for (const line of lines) {
      expect(await screen.findByText(line, { exact: false })).toBeVisible();
    }
    expect(screen.queryByText("Brieven worden niet automatisch verwerkt.")).not.toBeInTheDocument();
    expect(screen.queryByText(/automatisch verwerkt door de AI/)).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("tells a member that letters are not processed automatically", async () => {
    autopilotServer("external");
    server.use(asRole("member"));
    renderApp(path);
    expect(await screen.findByText("Brieven worden niet automatisch verwerkt.")).toBeVisible();
  });

  it("tells a member nothing is automatic while the server forbids it", async () => {
    autopilotServer("external", { allowed: false, auto_confirm: true });
    server.use(asRole("member"));
    renderApp(path);
    expect(await screen.findByText("Brieven worden niet automatisch verwerkt.")).toBeVisible();
  });

  it("lets an admin switch off a switch still stored on while the server forbids it", async () => {
    const state = autopilotServer("external", { allowed: false, auto_confirm: true });
    const user = userEvent.setup();
    renderApp(path);
    expect(
      await screen.findByText(
        "De beheerder van de server heeft automatisch verwerken uitgezet. Hier kun je het niet aanzetten.",
      ),
    ).toBeVisible();
    const confirm = await screen.findByRole("switch", { name: "Brieven zelf bevestigen" });
    expect(confirm).toBeChecked();
    // The one that is off cannot be switched on, so it is not offered.
    expect(
      screen.queryByRole("switch", { name: "Brieven zelf onder personen en labels zetten" }),
    ).not.toBeInTheDocument();

    await user.click(confirm);

    await waitFor(() =>
      expect(state.puts).toEqual([
        { auto_organise: false, auto_confirm: false, acknowledged: false },
      ]),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("switch")).not.toBeInTheDocument());
  });

  it("clears a refusal when the question is cancelled", async () => {
    autopilotServer("external");
    server.use(
      http.put(`${base}/ai/autopilot`, () =>
        HttpResponse.json(
          errorBody("ai_autopilot_forbidden", "De serverbeheerder heeft dit uitgezet."),
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("switch", { name: "Brieven zelf bevestigen" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Aanzetten" }));
    await within(dialog).findByRole("alert");
    await user.click(within(dialog).getByRole("button", { name: "Annuleren" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.queryByText("De serverbeheerder heeft dit uitgezet.")).not.toBeInTheDocument();
  });
});
