import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
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
const KEY = "nep-sleutel-voor-tests";
const MISTRAL = "https://api.mistral.ai/v1";
const NOTICE = "Je zet AI aan voor werkruimte Thuis. Lees dit eerst.\n\n- De tekst van elke brief.";

const suppliers = [
  {
    key: "mistral",
    name: "Mistral",
    country: "Frankrijk (Europese Unie)",
    host: "api.mistral.ai",
    default_model: "mistral-small-latest",
  },
  {
    key: "openai",
    name: "OpenAI",
    country: "Verenigde Staten",
    host: "api.openai.com",
    default_model: "",
  },
];

const on = {
  mode: "external",
  supplier: "mistral",
  host: "api.mistral.ai",
  since: "2026-11-17T14:39:59.238Z",
  has_api_key: true,
};

type Body = Record<string, unknown>;

/** A server whose AI state is `state.current`; it records what the page sends. */
function aiServer(initial: Body = { mode: "off" }) {
  const state = { current: initial, previews: [] as Body[], puts: [] as Body[] };
  server.use(
    ...loggedInHandlers(),
    http.get(`${base}/ai`, () => HttpResponse.json(state.current)),
    http.get(`${base}/ai/suppliers`, () => HttpResponse.json(suppliers)),
    http.get(`${base}/ai/autopilot`, () =>
      HttpResponse.json({ allowed: true, auto_organise: false, auto_confirm: false, notice: "" }),
    ),
    http.post(`${base}/ai/preview`, async ({ request }) => {
      const body = (await request.json()) as Body;
      state.previews.push(body);
      const external = body.mode === "external";
      const needs = external && state.current.mode !== "external";
      return HttpResponse.json({
        needs_consent: needs,
        notice: needs ? NOTICE : "",
        address: external ? MISTRAL : "",
      });
    }),
    http.put(`${base}/ai`, async ({ request }) => {
      const body = (await request.json()) as Body;
      state.puts.push(body);
      state.current = body.mode === "external" ? on : { mode: body.mode, has_api_key: false };
      return HttpResponse.json(state.current);
    }),
  );
  return state;
}

describe("AI settings", () => {
  it("shows a member the state and nothing to change", async () => {
    aiServer(on);
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "member" }])),
    );
    renderApp(path);
    expect(await screen.findByText(/AI aan: tekst van brieven gaat naar Mistral/)).toBeVisible();
    expect(
      screen.getByText("Alleen de beheerder van deze werkruimte kan dit veranderen."),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Opslaan" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
  });

  it("asks the administrator to read and agree before it switches on", async () => {
    const state = aiServer();
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("radio", { name: /Een AI-dienst op internet/ }));
    expect(screen.getByRole("radio", { name: /Mistral.*Frankrijk/ })).toBeChecked();
    await user.type(screen.getByLabelText(/Sleutel van de dienst/), KEY);
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    const dialog = await screen.findByRole("alertdialog", { name: "AI aanzetten?" });
    // The server's own text, line by line.
    expect(within(dialog).getByText(/Lees dit eerst/).textContent).toBe(NOTICE);
    expect(state.puts).toEqual([]);
    const agree = within(dialog).getByRole("button", { name: "AI aanzetten" });
    expect(agree).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox", { name: /Ik heb dit gelezen/ }));
    await user.click(agree);

    expect(await screen.findByText("Opgeslagen.")).toBeVisible();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(state.puts).toEqual([
      {
        mode: "external",
        supplier: "mistral",
        base_url: "",
        model: "",
        api_key: KEY,
        consent_address: MISTRAL,
      },
    ]);
    // The key is gone from the page, and the line says where the text goes now.
    expect(screen.getByLabelText(/Sleutel van de dienst/)).toHaveValue("");
    expect(await screen.findByText(/AI aan: tekst van brieven gaat naar Mistral/)).toBeVisible();
  });

  it("changes nothing when the administrator does not agree", async () => {
    const state = aiServer();
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("radio", { name: /Een AI-dienst op internet/ }));
    await user.type(screen.getByLabelText(/Sleutel van de dienst/), KEY);
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Annuleren" }));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(state.puts).toEqual([]);
    expect(screen.queryByText("Opgeslagen.")).not.toBeInTheDocument();
  });

  it("switches off without a question", async () => {
    const state = aiServer(on);
    const user = userEvent.setup();
    renderApp(path);
    expect(await screen.findByRole("radio", { name: /Een AI-dienst op internet/ })).toBeChecked();
    await user.click(screen.getByRole("radio", { name: /Geen AI/ }));
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    expect(await screen.findByText("Opgeslagen.")).toBeVisible();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(state.puts).toEqual([
      { mode: "off", supplier: "mistral", base_url: "", model: "", consent_address: null },
    ]);
    expect(await screen.findByText(/AI uit/)).toBeVisible();
  });

  it("keeps the stored key when only the model changes", async () => {
    const state = aiServer(on);
    const user = userEvent.setup();
    renderApp(path);
    expect(
      await screen.findByText("Er is al een sleutel opgeslagen. Laat dit leeg om die te houden."),
    ).toBeInTheDocument();
    await user.click(screen.getByText("Geavanceerd"));
    await user.type(screen.getByRole("textbox", { name: /Model/ }), "mistral-large-latest");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    expect(await screen.findByText("Opgeslagen.")).toBeVisible();
    expect(state.puts).toEqual([
      {
        mode: "external",
        supplier: "mistral",
        base_url: "",
        model: "mistral-large-latest",
        consent_address: null,
      },
    ]);
  });

  it("keeps the model when only the key is replaced", async () => {
    const state = aiServer({ ...on, supplier: "openai", host: "api.openai.com", model: "gpt-x" });
    const user = userEvent.setup();
    renderApp(path);
    expect(await screen.findByRole("textbox", { name: /Model/ })).toHaveValue("gpt-x");
    await user.type(screen.getByLabelText(/Sleutel van de dienst/), KEY);
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    expect(await screen.findByText("Opgeslagen.")).toBeVisible();
    expect(state.puts).toEqual([
      {
        mode: "external",
        supplier: "openai",
        base_url: "",
        model: "gpt-x",
        api_key: KEY,
        consent_address: null,
      },
    ]);
  });

  it("asks for a model in plain sight when the service has no default", async () => {
    const state = aiServer();
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("radio", { name: /Een AI-dienst op internet/ }));
    await user.click(screen.getByRole("radio", { name: /OpenAI/ }));
    // Not behind "Geavanceerd": a required field nobody sees would block saving in silence.
    const model = screen.getByRole("textbox", { name: /Model/ });
    expect(model).toBeVisible();
    expect(model).toBeRequired();
    await user.type(screen.getByLabelText(/Sleutel van de dienst/), KEY);
    await user.type(model, "gpt-x");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    await screen.findByRole("alertdialog");
    expect(state.previews).toEqual([
      { mode: "external", supplier: "openai", base_url: "", model: "gpt-x" },
    ]);
  });

  it("sends an own address as an OpenAI-compatible service", async () => {
    const state = aiServer();
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("radio", { name: /Een AI-dienst op internet/ }));
    await user.click(screen.getByRole("radio", { name: /Mistral/ }));
    await user.type(screen.getByLabelText(/Sleutel van de dienst/), KEY);
    await user.click(screen.getByText("Geavanceerd"));
    await user.type(
      screen.getByRole("textbox", { name: /Eigen adres/ }),
      "https://llm.example.com/v1",
    );
    await user.type(screen.getByRole("textbox", { name: /Model/ }), "m");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    await screen.findByRole("alertdialog");
    expect(state.previews).toEqual([
      { mode: "external", supplier: "openai", base_url: "https://llm.example.com/v1", model: "m" },
    ]);
  });

  it("says that an own address must be typed again", async () => {
    aiServer({ ...on, supplier: "custom", host: "llm.example.com", model: "m" });
    renderApp(path);
    expect(
      await screen.findByText(/gebruikt nu een eigen adres \(llm.example.com\)/),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: /Eigen adres/ })).toBeVisible();
  });

  it("shows the server's reason when the settings do not pass", async () => {
    const state = aiServer();
    server.use(
      http.post(`${base}/ai/preview`, () =>
        HttpResponse.json(
          errorBody("ai_settings_invalid", "Dit adres ligt niet op je eigen netwerk."),
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("radio", { name: /AI op je eigen netwerk/ }));
    await user.type(
      screen.getByRole("textbox", { name: /Adres van de AI op je netwerk/ }),
      "https://example.com/v1",
    );
    await user.type(screen.getByRole("textbox", { name: "Model" }), "qwen");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Dit adres ligt niet op je eigen netwerk.",
    );
    expect(state.puts).toEqual([]);
  });

  it("has an entry in the navigation for every member", async () => {
    aiServer();
    renderApp(path);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(within(nav).getByRole("link", { name: "AI" })).toBeInTheDocument();
    await screen.findByRole("button", { name: "Opslaan" });
  });
});
