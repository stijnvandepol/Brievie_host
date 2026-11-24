import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  asRole,
  errorBody,
  HttpResponse,
  homeVisibility,
  http,
  loggedInHandlers,
  people,
  peopleForMember,
  peopleHandlers,
  personAccount,
  renderApp,
  server,
  user,
  watchAlerts,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/mensen`;
const base = `*${API}/workspaces/${workspace.id}`;
const [anna, bram] = people as [(typeof people)[number], (typeof people)[number]];

function rowOf(name: string) {
  return screen.findByRole("row", { name: new RegExp(`^${name}`) });
}

describe("Mensen", () => {
  it("shows one nav item Mensen and no Leden or Personen", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Mensen" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: /hoofdmenu|navigatie/i });
    expect(within(nav).getByRole("link", { name: "Mensen" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Leden" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Personen" })).not.toBeInTheDocument();
  });

  it.each(["leden", "personen"])("the old path /%s opens Mensen", async (old) => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    const { router } = renderApp(`/w/${workspace.id}/${old}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Mensen" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(path);
  });

  it("lists people with their status words", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    expect(await rowOf("Anna")).toHaveTextContent("Actief");
    expect(await rowOf("Papa")).toHaveTextContent("Niet actief");
    expect(await rowOf("Oma")).toHaveTextContent("Uitgenodigd");
    expect(await rowOf("Kees")).toHaveTextContent("Uitgeschakeld");
  });

  it("shows Laatst ingelogd or Nooit ingelogd", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    expect(await rowOf("Anna")).toHaveTextContent(/Laatst ingelogd op 20 november 2026/);
    expect(await rowOf("Bram")).toHaveTextContent("Nooit ingelogd");
    expect(await rowOf("Kees")).toHaveTextContent(/Laatst ingelogd op 1 oktober 2026/);
    expect(screen.getByRole("columnheader", { name: "Laatst ingelogd" })).toBeInTheDocument();
  });

  it("filters by status", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    const u = userEvent.setup();
    renderApp(path);
    await rowOf("Papa");
    await u.selectOptions(screen.getByRole("combobox", { name: "Status" }), "Uitgenodigd");
    await waitFor(() =>
      expect(screen.queryByRole("row", { name: /^Papa/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("row", { name: /^Oma/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /^Anna/ })).not.toBeInTheDocument();
  });

  it("searches by name, also by how the name is written on letters", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    const u = userEvent.setup();
    renderApp(path);
    await rowOf("Papa");
    await u.type(screen.getByRole("searchbox", { name: "Zoek op naam" }), "de vries");
    await waitFor(() =>
      expect(screen.queryByRole("row", { name: /^Anna/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("row", { name: /^Papa/ })).toBeInTheDocument();
  });

  it("a member sees no address, last sign-in, invitation or actions", async () => {
    server.use(asRole("member"), ...loggedInHandlers(), ...peopleHandlers(peopleForMember));
    renderApp(path);
    expect(await rowOf("Oma")).toHaveTextContent("Niet actief");
    expect(screen.queryByRole("columnheader", { name: "Laatst ingelogd" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Nooit ingelogd/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Uitnodigen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Uitschakelen/ })).not.toBeInTheDocument();
    const filter = screen.getByRole("combobox", { name: "Status" });
    expect(within(filter).queryByRole("option", { name: "Uitgenodigd" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Persoon toevoegen" })).toBeInTheDocument();
  });

  it("a viewer cannot add a person", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...peopleHandlers(peopleForMember));
    renderApp(path);
    await rowOf("Papa");
    expect(screen.queryByRole("button", { name: "Persoon toevoegen" })).not.toBeInTheDocument();
  });

  it("archived people show only under Gearchiveerd", async () => {
    const opa = personAccount({
      id: "a6666666-6666-4666-8666-666666666666",
      display_name: "Opa",
      archived: true,
    });
    server.use(...loggedInHandlers(), ...peopleHandlers([...people, opa]));
    const u = userEvent.setup();
    renderApp(path);
    await rowOf("Papa");
    expect(screen.queryByRole("row", { name: /^Opa/ })).not.toBeInTheDocument();
    await u.selectOptions(screen.getByRole("combobox", { name: "Status" }), "Gearchiveerd");
    expect(await rowOf("Opa")).toHaveTextContent("Gearchiveerd");
    expect(screen.queryByRole("row", { name: /^Papa/ })).not.toBeInTheDocument();
  });

  it("departments are not listed, but a person's departments are", async () => {
    const finance = "c1111111-1111-4111-8111-111111111111";
    server.use(
      http.get(`${base}/persons`, () =>
        HttpResponse.json([
          {
            id: finance,
            display_name: "Finance",
            name_variants: [],
            archived: false,
            document_count: 0,
            user_id: null,
            kind: "department",
          },
        ]),
      ),
      http.get(`${base}/visibility`, () =>
        HttpResponse.json({
          ...homeVisibility,
          kind: "business",
          everyone_sees_all: false,
          departments_enabled: true,
        }),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers([{ ...bram, department_ids: [finance] }]),
    );
    renderApp(path);
    expect(await rowOf("Bram")).toHaveTextContent("Finance");
    expect(screen.queryByRole("row", { name: /^Finance/ })).not.toBeInTheDocument();
  });

  it("adds a person and shows the new row", async () => {
    let list = [...people];
    let sent: unknown = null;
    server.use(
      http.get(`${base}/people`, () => HttpResponse.json(list)),
      http.post(`${base}/persons`, async ({ request }) => {
        sent = await request.json();
        list = [
          ...list,
          personAccount({ id: "a7777777-7777-4777-8777-777777777777", display_name: "Opa" }),
        ];
        return HttpResponse.json({}, { status: 201 });
      }),
      ...loggedInHandlers(),
      ...peopleHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Persoon toevoegen" }));
    const dialog = await screen.findByRole("dialog", { name: "Persoon toevoegen" });
    await u.type(within(dialog).getByRole("textbox", { name: "Naam" }), "Opa");
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    expect(await rowOf("Opa")).toHaveTextContent("Niet actief");
    expect(sent).toMatchObject({ display_name: "Opa", kind: "person" });
  });

  it("shows the server's sentence for a duplicate name", async () => {
    server.use(
      ...loggedInHandlers(),
      ...peopleHandlers(),
      http.post(`${base}/persons`, () =>
        HttpResponse.json(errorBody("person_exists", "Er is al een persoon met deze naam."), {
          status: 409,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Persoon toevoegen" }));
    const dialog = await screen.findByRole("dialog", { name: "Persoon toevoegen" });
    await u.type(within(dialog).getByRole("textbox", { name: "Naam" }), "Papa");
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Er is al een persoon met deze naam.",
    );
  });

  it("the name links to the person's page", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    const row = await rowOf("Papa");
    expect(within(row).getByRole("link", { name: /Papa/ })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/mensen/${people[2]?.id}`,
    );
  });

  it("the only admin cannot leave or step down, and reads why", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    const own = await rowOf("Anna");
    expect(
      within(own).getByText(
        "Je bent de enige beheerder. Maak eerst iemand anders beheerder als je wilt stoppen.",
      ),
    ).toBeInTheDocument();
    expect(within(own).queryByRole("button")).not.toBeInTheDocument();
  });

  it("with a second admin, an admin may leave and change their own role", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers([anna, { ...bram, role: "admin" }]));
    renderApp(path);
    const own = await rowOf("Anna");
    expect(within(own).getByRole("button", { name: "Werkruimte verlaten" })).toBeInTheDocument();
    expect(within(own).getByRole("button", { name: "Rol wijzigen: Anna" })).toBeInTheDocument();
  });

  it("a refused leave shows the server's sentence", async () => {
    server.use(
      ...loggedInHandlers(),
      ...peopleHandlers([anna, { ...bram, role: "admin" }]),
      http.post(`${base}/leave`, () =>
        HttpResponse.json(
          errorBody(
            "last_admin",
            "Een werkruimte heeft altijd minstens één beheerder. Maak eerst iemand anders beheerder.",
          ),
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Werkruimte verlaten?" });
    await u.click(within(dialog).getByRole("button", { name: "Verlaten" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Een werkruimte heeft altijd minstens één beheerder.",
    );
  });

  it("after leaving the last workspace the app lands on the sign-in page", async () => {
    let signedIn = true;
    const refuse = () =>
      HttpResponse.json(errorBody("forbidden", "Geen toegang."), { status: 403 });
    server.use(
      http.get(`*${API}/me`, () =>
        signedIn
          ? HttpResponse.json(user)
          : HttpResponse.json(errorBody("not_authenticated", "Log eerst in."), { status: 401 }),
      ),
      http.get(`${base}/people`, () =>
        signedIn ? HttpResponse.json([anna, { ...bram, role: "admin" }]) : refuse(),
      ),
      ...loggedInHandlers(),
      ...peopleHandlers([anna, { ...bram, role: "admin" }]),
      http.post(`${base}/leave`, () => {
        signedIn = false;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Werkruimte verlaten" }));
    const dialog = await screen.findByRole("alertdialog");
    const alerts = watchAlerts();
    await u.click(within(dialog).getByRole("button", { name: "Verlaten" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inloggen" })).toBeInTheDocument();
    alerts.stop();
    expect(alerts.seen()).toBe(false);
  });

  it("changes a role with three plain choices", async () => {
    let body: unknown = null;
    server.use(
      asRole("admin", "business"),
      ...loggedInHandlers(),
      ...peopleHandlers(),
      http.put(`${base}/members/${bram.user_id}/role`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({});
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    expect(await rowOf("Bram")).toHaveTextContent("Lid");
    await u.click(screen.getByRole("button", { name: "Rol wijzigen: Bram" }));
    const dialog = await screen.findByRole("dialog", { name: /Rol van Bram/ });
    expect(within(dialog).getByRole("radio", { name: "Lid" })).toBeChecked();
    await u.click(within(dialog).getByRole("radio", { name: "Alleen lezen" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(body).toMatchObject({ role: "viewer" }));
  });

  it("offers the account menu only where the server allows it", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    const bramRow = await rowOf("Bram");
    expect(within(bramRow).getByRole("button", { name: "Account: Bram" })).toBeInTheDocument();
    expect(within(await rowOf("Anna")).queryByRole("button", { name: /Account/ })).toBeNull();
  });

  it("shows the export of my own archive", async () => {
    server.use(...loggedInHandlers(), ...peopleHandlers());
    renderApp(path);
    expect(await screen.findByRole("heading", { name: "Exporteren" })).toBeInTheDocument();
  });
});
