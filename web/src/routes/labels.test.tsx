import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  aiHandlers,
  belasting,
  errorBody,
  HttpResponse,
  http,
  labelHandlers,
  loggedInHandlers,
  renderApp,
  sales,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/labels`;
const base = `*${API}/workspaces/${workspace.id}`;

const presetKeys = [
  "belastingdienst",
  "zorg",
  "verzekering",
  "wonen",
  "energie",
  "auto",
  "bank",
  "inkomen",
  "gemeente",
  "abonnementen",
];
const fullPreset = presetKeys.map((key, index) => ({
  id: `f000000${index}-0000-4000-8000-000000000000`,
  name: key,
  color: "grey",
  system_key: key,
  document_count: 0,
}));

function asViewer() {
  return http.get(`*${API}/workspaces`, () =>
    HttpResponse.json([{ ...workspace, role: "viewer" }]),
  );
}

describe("labels", () => {
  it("lists the labels with their number of letters", async () => {
    server.use(...loggedInHandlers(), ...labelHandlers());
    renderApp(path);
    const row = (await screen.findByRole("rowheader", { name: /Belastingdienst/ })).closest("tr");
    expect(within(row as HTMLElement).getAllByRole("cell")[0]).toHaveTextContent("2");
    expect(screen.getByRole("rowheader", { name: /Sales/ })).toBeInTheDocument();
  });

  it("adds a label with a colour", async () => {
    const posts: unknown[] = [];
    const list = [belasting];
    server.use(
      http.post(`${base}/labels`, async ({ request }) => {
        const body = (await request.json()) as { name: string; color: string };
        posts.push(body);
        const label = { ...sales, id: "d3333333-3333-4333-8333-333333333333", ...body };
        list.push(label);
        return HttpResponse.json(label, { status: 201 });
      }),
      http.get(`${base}/labels`, () => HttpResponse.json(list)),
      ...loggedInHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Label toevoegen" }));
    await user.type(screen.getByRole("textbox", { name: "Naam" }), "Garantie");
    const colours = within(screen.getByRole("radiogroup", { name: "Kleur" }))
      .getAllByRole("radio")
      .map((radio) => (radio as HTMLInputElement).value);
    expect(colours).toEqual(["grey", "blue", "teal", "green", "yellow", "orange", "red", "purple"]);
    await user.click(screen.getByRole("radio", { name: "Oranje" }));
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("rowheader", { name: /Garantie/ })).toBeInTheDocument();
    expect(posts).toEqual([{ name: "Garantie", color: "orange" }]);
  });

  it("adds several labels at once and skips the ones that exist", async () => {
    const posts: string[] = [];
    const list = [belasting, sales];
    server.use(
      http.post(`${base}/labels`, async ({ request }) => {
        const body = (await request.json()) as { name: string; color: string };
        posts.push(body.name);
        list.push({ ...sales, id: `n-${body.name}`, ...body });
        return HttpResponse.json({ ...sales, ...body }, { status: 201 });
      }),
      http.get(`${base}/labels`, () => HttpResponse.json(list)),
      ...loggedInHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Meerdere tegelijk toevoegen" }));
    await user.type(
      screen.getByRole("textbox", { name: "Namen" }),
      "sales, Finance, HR{Enter}Legal",
    );
    await user.click(screen.getByRole("button", { name: "Toevoegen" }));
    expect(await screen.findByText("3 labels toegevoegd, 1 bestond al")).toBeInTheDocument();
    expect(posts).toEqual(["Finance", "HR", "Legal"]);
    await user.click(screen.getByRole("button", { name: "Sluiten" }));
    expect(await screen.findByRole("rowheader", { name: /Finance/ })).toBeInTheDocument();
  });

  it("asks for confirmation before deleting and names the number of letters", async () => {
    let deleted = false;
    server.use(
      http.delete(`${base}/labels/${belasting.id}`, () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      ...labelHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: /Verwijderen: Belastingdienst/ }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("2 brieven");
    expect(deleted).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    await waitFor(() => expect(deleted).toBe(true));
  });

  it("shows the server's sentence when a label is in use by a rule", async () => {
    server.use(
      http.delete(`${base}/labels/${sales.id}`, () =>
        HttpResponse.json(errorBody("label_in_use", "Regel 'Sales' gebruikt dit label."), {
          status: 409,
        }),
      ),
      ...loggedInHandlers(),
      ...labelHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: /Verwijderen: Sales/ }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Regel 'Sales' gebruikt dit label.");
  });

  it("offers the start set while preset labels are missing and applies it", async () => {
    let applied = 0;
    server.use(
      http.post(`${base}/labels/preset`, () => {
        applied += 1;
        server.use(http.get(`${base}/labels`, () => HttpResponse.json(fullPreset)));
        return HttpResponse.json(fullPreset);
      }),
      ...loggedInHandlers(),
      ...labelHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Startset toevoegen" }));
    await waitFor(() => expect(applied).toBe(1));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Startset toevoegen" })).not.toBeInTheDocument(),
    );
  });

  it("hides the start set when every preset key exists", async () => {
    server.use(...loggedInHandlers(), ...labelHandlers(fullPreset));
    renderApp(path);
    await screen.findByRole("rowheader", { name: /zorg/ });
    expect(screen.queryByRole("button", { name: "Startset toevoegen" })).not.toBeInTheDocument();
  });

  it("counts a business start set as nine labels", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, kind: "business" }])),
      ...loggedInHandlers(),
      ...labelHandlers(fullPreset.slice(0, 9)),
    );
    renderApp(path);
    await screen.findByRole("rowheader", { name: /zorg/ });
    expect(screen.queryByRole("button", { name: "Startset toevoegen" })).not.toBeInTheDocument();
  });

  it("hides the start set for a member who is not an admin", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "member" }])),
      ...loggedInHandlers(),
      ...labelHandlers(),
    );
    renderApp(path);
    await screen.findByRole("rowheader", { name: /Belastingdienst/ });
    expect(screen.getByRole("button", { name: "Label toevoegen" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Startset toevoegen" })).not.toBeInTheDocument();
  });

  it("gives a viewer no controls to change labels", async () => {
    server.use(asViewer(), ...loggedInHandlers(), ...labelHandlers());
    renderApp(path);
    await screen.findByRole("rowheader", { name: /Belastingdienst/ });
    for (const name of [
      "Label toevoegen",
      "Meerdere tegelijk toevoegen",
      "Startset toevoegen",
      /Wijzigen/,
      /Verwijderen/,
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("says the names go along when the AI service is external", async () => {
    server.use(...aiHandlers("external"), ...loggedInHandlers(), ...labelHandlers());
    renderApp(path);
    expect(
      await screen.findByText(
        "Je gebruikt een externe AI-dienst. De namen op deze pagina gaan mee met elke brief die de AI leest.",
      ),
    ).toBeInTheDocument();
  });

  it.each(["off", "local"])("says nothing about names when the AI is %s", async (mode) => {
    server.use(...aiHandlers(mode), ...loggedInHandlers(), ...labelHandlers());
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Labels" });
    await screen.findByRole("region", { name: "Labels" });
    expect(screen.queryByText(/externe AI-dienst/)).not.toBeInTheDocument();
  });
});
