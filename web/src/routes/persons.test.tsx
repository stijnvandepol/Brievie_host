import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
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
