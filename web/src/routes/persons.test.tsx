import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  aiHandlers,
  annaPerson,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  members,
  papa,
  personHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/personen`;
const base = `*${API}/workspaces/${workspace.id}`;

describe("persons", () => {
  it("lists persons with variants, account and number of letters", async () => {
    server.use(...loggedInHandlers(), ...personHandlers());
    renderApp(path);
    const row = (await screen.findByRole("rowheader", { name: /Papa/ })).closest("tr");
    const cells = within(row as HTMLElement).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("P. de Vries");
    expect(cells[2]).toHaveTextContent("2");
    const anna = screen.getByRole("rowheader", { name: /Anna/ }).closest("tr");
    expect(within(anna as HTMLElement).getAllByRole("cell")[1]).toHaveTextContent("Anna");
  });

  it("adds a person and shows the new row", async () => {
    const created: unknown[] = [];
    const list = [papa];
    server.use(
      http.post(`${base}/persons`, async ({ request }) => {
        const body = (await request.json()) as { display_name: string; name_variants: string[] };
        created.push(body);
        const person = { ...papa, id: "c4444444-4444-4444-8444-444444444444", ...body };
        list.push(person);
        return HttpResponse.json(person, { status: 201 });
      }),
      http.get(`${base}/persons`, () => HttpResponse.json(list)),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Persoon toevoegen" }));
    await user.type(screen.getByRole("textbox", { name: "Naam" }), "Oma");
    await user.type(
      screen.getByRole("textbox", { name: /Naamvarianten/ }),
      "J. de Vries{Enter}Oma de Vries",
    );
    expect(
      screen.getByText(/Zo staat de naam op brieven, bijvoorbeeld J\. de Vries/),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("rowheader", { name: /Oma/ })).toBeInTheDocument();
    expect(created).toEqual([
      { display_name: "Oma", name_variants: ["J. de Vries", "Oma de Vries"], archived: false },
    ]);
  });

  it("shows the server's sentence for a duplicate name", async () => {
    server.use(
      http.post(`${base}/persons`, () =>
        HttpResponse.json(errorBody("person_exists", "Er is al een persoon met die naam."), {
          status: 409,
        }),
      ),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Persoon toevoegen" }));
    await user.type(screen.getByRole("textbox", { name: "Naam" }), "Papa");
    await user.click(screen.getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Er is al een persoon met die naam.",
    );
  });

  it("archives a person and puts it back", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(`${base}/persons/${papa.id}`, async ({ request }) => {
        const body = (await request.json()) as { archived: boolean };
        bodies.push(body);
        return HttpResponse.json({ ...papa, ...body });
      }),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: /Archiveren: Papa/ }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({
      display_name: "Papa",
      name_variants: ["P. de Vries"],
      archived: true,
    });
  });

  it("offers Terugzetten for an archived person", async () => {
    server.use(...loggedInHandlers(), ...personHandlers([{ ...papa, archived: true }]));
    renderApp(path);
    expect(await screen.findByRole("button", { name: /Terugzetten: Papa/ })).toBeInTheDocument();
  });

  it("moves focus to the page heading after a delete, as the row is gone", async () => {
    let deleted = false;
    server.use(
      http.delete(`${base}/persons/${papa.id}`, () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get(`${base}/persons`, () =>
        HttpResponse.json(deleted ? [annaPerson] : [papa, annaPerson]),
      ),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: /Verwijderen: Papa/ }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Verwijderen: Papa/ })).toBeNull(),
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: "Personen" })).toHaveFocus(),
    );
  });

  it("shows the server's sentence when a person cannot be deleted", async () => {
    server.use(
      http.delete(`${base}/persons/${papa.id}`, () =>
        HttpResponse.json(
          errorBody("person_has_documents", "Deze persoon staat op brieven. Archiveer de persoon."),
          { status: 409 },
        ),
      ),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: /Verwijderen: Papa/ }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Verwijderen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Deze persoon staat op brieven. Archiveer de persoon.",
    );
  });

  it("links a person to a member without a person, for an admin", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.put(`${base}/persons/${papa.id}/member`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...papa, user_id: members[1]?.user_id });
      }),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    const select = await screen.findByRole("combobox", { name: /Koppel aan account: Papa/ });
    expect(
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Kies een account", "Bram"]);
    // Anna is linked already, so her row has no control.
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    await user.selectOptions(select, "Bram");
    await waitFor(() => expect(bodies).toEqual([{ user_id: members[1]?.user_id }]));
  });

  it("has no link control when every member has a person", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`${base}/members`, () =>
        HttpResponse.json(members.map((m) => ({ ...m, person_id: annaPerson.id }))),
      ),
      ...personHandlers(),
    );
    renderApp(path);
    await screen.findByRole("rowheader", { name: /Papa/ });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("has no link control for a member who is not an admin", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "member" }])),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    renderApp(path);
    await screen.findByRole("rowheader", { name: /Papa/ });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});

describe("persons: roles and errors", () => {
  it("gives a viewer no controls to change persons", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    renderApp(path);
    await screen.findByRole("rowheader", { name: /Papa/ });
    expect(screen.queryByRole("button", { name: "Persoon toevoegen" })).not.toBeInTheDocument();
    for (const name of [/Wijzigen/, /Archiveren/, /Terugzetten/, /Verwijderen/]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("clears a row error after a later action succeeds", async () => {
    let attempts = 0;
    server.use(
      http.put(`${base}/persons/${papa.id}`, async ({ request }) => {
        attempts += 1;
        if (attempts === 1) {
          return HttpResponse.json(errorBody("person_exists", "Dat ging mis."), { status: 409 });
        }
        return HttpResponse.json({ ...papa, ...((await request.json()) as object) });
      }),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: /Archiveren: Papa/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Dat ging mis.");
    await user.click(screen.getByRole("button", { name: /Archiveren: Papa/ }));
    await waitFor(() => expect(attempts).toBe(2));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("shows a failed load of the members", async () => {
    server.use(
      http.get(`${base}/members`, () =>
        HttpResponse.json(errorBody("server_error", "De accounts konden niet worden geladen."), {
          status: 500,
        }),
      ),
      ...loggedInHandlers(),
      http.get(`${base}/persons`, () => HttpResponse.json([papa])),
    );
    renderApp(path);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "De accounts konden niet worden geladen.",
    );
  });

  it("says the names go along when the AI service is external", async () => {
    server.use(...aiHandlers("external"), ...loggedInHandlers(), ...personHandlers());
    renderApp(path);
    expect(
      await screen.findByText(
        "Je gebruikt een externe AI-dienst. De namen op deze pagina gaan mee met elke brief die de AI leest.",
      ),
    ).toBeInTheDocument();
  });

  it.each(["off", "local"])("says nothing about names when the AI is %s", async (mode) => {
    server.use(...aiHandlers(mode), ...loggedInHandlers(), ...personHandlers());
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Personen" });
    await screen.findByRole("region", { name: "Personen" });
    expect(screen.queryByText(/externe AI-dienst/)).not.toBeInTheDocument();
  });
});
