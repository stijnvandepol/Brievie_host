import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  annaPerson,
  asRole,
  errorBody,
  exportState,
  HttpResponse,
  http,
  loggedInHandlers,
  members,
  noMenuCounts,
  papa,
  people,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

const path = `/w/${workspace.id}/afdelingen`;
const base = `*${API}/workspaces/${workspace.id}`;
const bram = "b3333333-3333-4333-8333-333333333333";

type Person = components["schemas"]["PersonOut"];
type Visibility = components["schemas"]["VisibilityOut"];

const finance: Person = {
  id: "e1111111-1111-4111-8111-111111111111",
  display_name: "Finance",
  name_variants: ["Financiën"],
  archived: false,
  document_count: 7,
  user_id: null,
  kind: "department",
  member_user_ids: [user.id, bram],
  lead_user_ids: [bram],
};

const hr: Person = {
  ...finance,
  id: "e2222222-2222-4222-8222-222222222222",
  display_name: "HR",
  name_variants: [],
  document_count: 0,
  member_user_ids: [],
  lead_user_ids: [],
};

const visibility: Visibility = {
  kind: "business",
  everyone_sees_all: false,
  departments_enabled: true,
  admins_read_all: false,
  sorter_user_ids: [],
  sentences: [],
  summary: [],
};

function handlers({
  persons = [papa, annaPerson, finance, hr],
  shown = visibility,
  role = "admin",
}: {
  persons?: Person[];
  shown?: Visibility;
  role?: "admin" | "member" | "viewer";
} = {}) {
  return [
    asRole(role, shown.kind === "home" ? "home" : "business"),
    http.get(`${base}/visibility`, () => HttpResponse.json(shown)),
    http.get(`${base}/persons`, () => HttpResponse.json(persons)),
    http.get(`${base}/members`, () => HttpResponse.json(members)),
    http.get(`${base}/people`, () => HttpResponse.json(people)),
    http.get(`${base}/invitations`, () => HttpResponse.json([])),
    http.get(`${base}/my-export`, () => HttpResponse.json(exportState())),
    ...loggedInHandlers(),
  ];
}

describe("afdelingen", () => {
  it("lists departments with members, leads and letter counts", async () => {
    server.use(...handlers());
    renderApp(path);
    const table = await screen.findByRole("region", { name: "Afdelingen" });
    const row = (await within(table).findByRole("rowheader", { name: /Finance/ })).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Anna, Bram (Leidinggevende)")).toBeInTheDocument();
    expect(within(row).getByText("7")).toBeInTheDocument();
    expect(within(table).queryByRole("rowheader", { name: /Papa/ })).toBeNull();
    expect(
      screen.getByText("Wil je post scheiden binnen je organisatie? Maak afdelingen."),
    ).toBeInTheDocument();
  });

  it("making a department adds its members after a preview", async () => {
    let created: unknown = null;
    const added: unknown[] = [];
    server.use(
      ...handlers(),
      http.post(`${base}/persons`, async ({ request }) => {
        created = await request.json();
        return HttpResponse.json(
          { ...hr, id: "e3333333-3333-4333-8333-333333333333", display_name: "Inkoop" },
          {
            status: 201,
          },
        );
      }),
      http.post(`${base}/persons/:personId/members`, async ({ request }) => {
        const body = (await request.json()) as { confirmed: boolean };
        added.push(body);
        if (!body.confirmed) {
          return HttpResponse.json(
            errorBody(
              "confirmation_required",
              "Bevestig eerst dat deze mensen meer post gaan zien.",
            ),
            { status: 409 },
          );
        }
        return HttpResponse.json(hr);
      }),
      http.post(`${base}/visibility/preview`, () =>
        HttpResponse.json({
          changes: [{ user_id: bram, name: "Bram", gains: ["post van Inkoop"], losses: [] }],
          letter_count: 0,
          needs_confirmation: true,
          sentences: [],
          preview_token: "tok",
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Afdeling maken" }));
    const dialog = await screen.findByRole("dialog", { name: "Afdeling maken" });
    await u.type(within(dialog).getByRole("textbox", { name: "Naam" }), "Inkoop");
    await u.click(within(dialog).getByRole("checkbox", { name: "Bram" }));
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    const preview = await screen.findByRole("alertdialog");
    expect(within(preview).getByText("post van Inkoop", { exact: false })).toBeInTheDocument();
    await u.click(within(preview).getByRole("button", { name: "Ja, voeg Bram toe aan Inkoop" }));
    await waitFor(() => expect(added).toHaveLength(2));
    expect(created).toMatchObject({ kind: "department", display_name: "Inkoop" });
    expect(added[1]).toMatchObject({ user_id: bram, confirmed: true, preview_token: "tok" });
  });

  it("adding yourself shows the refusal for a department with private post", async () => {
    server.use(
      ...handlers(),
      http.post(`${base}/persons/${hr.id}/members`, () =>
        HttpResponse.json(
          errorBody(
            "cannot_grant_self",
            "Je kunt jezelf geen toegang geven tot privé-post. Vraag een andere beheerder.",
          ),
          { status: 409 },
        ),
      ),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Leden: HR" }));
    const dialog = await screen.findByRole("dialog", { name: "Leden van HR" });
    await u.click(within(dialog).getByRole("checkbox", { name: "Anna" }));
    expect(
      await within(dialog).findByText(
        "Je kunt jezelf geen toegang geven tot privé-post. Vraag een andere beheerder.",
      ),
    ).toBeInTheDocument();
  });

  it("after a member leaves, the workspace's letters and counts are asked again", async () => {
    let counted = 0;
    server.use(
      // The menu's numbers (one call for the whole menu).
      http.get(`${base}/menu/counts`, () => {
        counted += 1;
        return HttpResponse.json(noMenuCounts);
      }),
      http.delete(`${base}/persons/${finance.id}/members/${bram}`, () =>
        HttpResponse.json(finance),
      ),
      ...handlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Leden: Finance" }));
    const dialog = await screen.findByRole("dialog", { name: "Leden van Finance" });
    await waitFor(() => expect(counted).toBeGreaterThan(0));
    const before = counted;
    await u.click(within(dialog).getByRole("checkbox", { name: "Bram" }));
    await waitFor(() => expect(counted).toBeGreaterThan(before));
  });

  it("a lead is marked Leidinggevende", async () => {
    let body: unknown = null;
    server.use(
      ...handlers(),
      http.put(`${base}/persons/${finance.id}/members/${user.id}`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(finance);
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Leden: Finance" }));
    const dialog = await screen.findByRole("dialog", { name: "Leden van Finance" });
    expect(within(dialog).getByRole("checkbox", { name: "Bram is leidinggevende" })).toBeChecked();
    await u.click(within(dialog).getByRole("checkbox", { name: "Anna is leidinggevende" }));
    await waitFor(() => expect(body).toEqual({ is_lead: true }));
  });

  it("a department with letters can be archived but not deleted", async () => {
    let archived: unknown = null;
    server.use(
      ...handlers(),
      http.put(`${base}/persons/${finance.id}`, async ({ request }) => {
        archived = await request.json();
        return HttpResponse.json({ ...finance, archived: true });
      }),
    );
    const u = userEvent.setup();
    renderApp(path);
    expect(await screen.findByRole("button", { name: "Verwijderen: HR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verwijderen: Finance" })).toBeNull();
    await u.click(screen.getByRole("button", { name: "Opbergen: Finance" }));
    await waitFor(() => expect(archived).toMatchObject({ archived: true, kind: "department" }));
  });

  it("the page is absent while departments are off", async () => {
    server.use(...handlers({ shown: { ...visibility, departments_enabled: false } }));
    renderApp(`/w/${workspace.id}/mensen`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    await within(nav).findByRole("link", { name: "Mensen" });
    expect(within(nav).queryByRole("link", { name: "Afdelingen" })).toBeNull();
  });

  it("is in the navigation while departments are on, and Mensen leaves them out", async () => {
    server.use(...handlers());
    renderApp(`/w/${workspace.id}/mensen`);
    const nav = await screen.findByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Afdelingen" })).toHaveAttribute(
      "href",
      path,
    );
    await screen.findByRole("rowheader", { name: /Papa/ });
    expect(screen.queryByRole("rowheader", { name: /Finance/ })).toBeNull();
  });

  it("a Thuis workspace calls them Groepen", async () => {
    server.use(...handlers({ shown: { ...visibility, kind: "home" } }));
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Groepen" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Groep maken" })).toBeInTheDocument();
  });

  it("a member reads the list without buttons", async () => {
    server.use(...handlers({ role: "member" }));
    renderApp(path);
    await screen.findByRole("rowheader", { name: /Finance/ });
    expect(screen.queryByRole("button", { name: "Afdeling maken" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Leden: Finance" })).toBeNull();
  });
});
