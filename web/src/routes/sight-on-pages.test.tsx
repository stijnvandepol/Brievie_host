/**
 * Who sees what on Labels, Leden and Automatisering (plan Task 45): department labels,
 * sorters, departments per member, the role words, and every change that widens sight
 * through the preview.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  annaPerson,
  apkRule,
  asRole,
  belasting,
  errorBody,
  exportState,
  HttpResponse,
  http,
  labelHandlers,
  loggedInHandlers,
  members,
  papa,
  renderApp,
  sales,
  server,
  user,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;
const bram = "b3333333-3333-4333-8333-333333333333";

type Person = components["schemas"]["PersonOut"];
type Visibility = components["schemas"]["VisibilityOut"];
type Member = components["schemas"]["MemberOut"];

const finance: Person = {
  id: "e1111111-1111-4111-8111-111111111111",
  display_name: "Finance",
  name_variants: [],
  archived: false,
  document_count: 1,
  user_id: null,
  kind: "department",
  member_user_ids: [user.id, bram],
  lead_user_ids: [user.id],
};

const ownPost: Visibility = {
  kind: "business",
  everyone_sees_all: false,
  departments_enabled: true,
  admins_read_all: false,
  sorter_user_ids: [],
  sentences: [],
  summary: [],
};

const office: Member[] = [
  { ...(members[0] as Member), department_ids: [finance.id] },
  { ...(members[1] as Member), department_ids: [finance.id] },
  {
    user_id: "c4444444-4444-4444-8444-444444444444",
    name: "Carla",
    role: "viewer",
    person_id: null,
    can_manage_account: false,
    sorts_post: false,
    department_ids: [],
  },
];

function world({
  role = "admin",
  shown = ownPost,
  list = office,
}: {
  role?: "admin" | "member" | "viewer";
  shown?: Visibility;
  list?: Member[];
} = {}) {
  return [
    asRole(role, "business"),
    http.get(`${base}/visibility`, () => HttpResponse.json(shown)),
    http.get(`${base}/persons`, () => HttpResponse.json([papa, annaPerson, finance])),
    http.get(`${base}/members`, () => HttpResponse.json(list)),
    http.get(`${base}/invitations`, () => HttpResponse.json([])),
    http.get(`${base}/my-export`, () => HttpResponse.json(exportState())),
    ...loggedInHandlers(),
  ];
}

function confirmFirst(path: string, seen: { confirmed: boolean; preview_token: string }[]) {
  return async ({ request }: { request: Request }) => {
    const body = (await request.json()) as { confirmed: boolean; preview_token: string };
    seen.push(body);
    if (!body.confirmed) {
      return HttpResponse.json(
        errorBody("confirmation_required", "Bevestig eerst dat deze mensen meer post gaan zien."),
        { status: 409 },
      );
    }
    void path;
    return HttpResponse.json({ ...office[1], role: "admin" });
  };
}

function gains(name: string, phrase: string, token = "tok") {
  return http.post(`${base}/visibility/preview`, () =>
    HttpResponse.json({
      changes: [{ user_id: bram, name, gains: [phrase], losses: [] }],
      letter_count: 4,
      needs_confirmation: true,
      sentences: [],
      preview_token: token,
    }),
  );
}

describe("labels of a department", () => {
  it("labels show Van Iedereen or the department", async () => {
    server.use(...labelHandlers([belasting, { ...sales, department_id: finance.id }]), ...world());
    renderApp(`/w/${workspace.id}/labels`);
    const table = await screen.findByRole("region", { name: "Labels" });
    expect(await within(table).findByRole("columnheader", { name: "Van" })).toBeInTheDocument();
    const salesRow = within(table).getByRole("rowheader", { name: "Sales" }).closest("tr");
    expect(await within(salesRow as HTMLElement).findByText("Finance")).toBeInTheDocument();
    const taxRow = within(table).getByRole("rowheader", { name: "Belastingdienst" }).closest("tr");
    expect(within(taxRow as HTMLElement).getByText("Iedereen")).toBeInTheDocument();
  });

  it("a lead can create a label for their department", async () => {
    let body: unknown = null;
    server.use(
      ...labelHandlers(),
      http.post(`${base}/labels`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(errorBody("label_exists", "Er is al een label met deze naam."), {
          status: 409,
        });
      }),
      ...world({ role: "member" }),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/labels`);
    await u.click(await screen.findByRole("button", { name: "Label toevoegen" }));
    const dialog = await screen.findByRole("dialog", { name: "Label toevoegen" });
    expect(
      within(dialog).getByText("Noem een label niet naar een persoon of een gevoelig onderwerp."),
    ).toBeInTheDocument();
    await u.type(within(dialog).getByRole("textbox", { name: "Naam" }), "Facturen");
    await u.selectOptions(await within(dialog).findByRole("combobox", { name: "Van" }), "Finance");
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() =>
      expect(body).toMatchObject({ name: "Facturen", department_id: finance.id }),
    );
    // The name is taken: the department's name in front is offered.
    await u.click(await within(dialog).findByRole("button", { name: "Finance: Facturen" }));
    expect(within(dialog).getByRole("textbox", { name: "Naam" })).toHaveValue("Finance: Facturen");
  });
});

describe("Leden", () => {
  it("Verdeelt post switches with a preview", async () => {
    const seen: { confirmed: boolean; preview_token: string }[] = [];
    server.use(
      http.put(`${base}/members/${bram}/sorts-post`, confirmFirst("sorts", seen)),
      gains("Bram", "post zonder naam"),
      ...world(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/leden`);
    await u.click(await screen.findByRole("switch", { name: "Verdeelt post: Bram" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("post zonder naam", { exact: false })).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Ja, Bram verdeelt post" }));
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[1]).toMatchObject({ sorts_post: true, confirmed: true, preview_token: "tok" });
  });

  it("a viewer cannot be a sorter", async () => {
    server.use(...world());
    renderApp(`/w/${workspace.id}/leden`);
    const toggle = await screen.findByRole("switch", { name: "Verdeelt post: Carla" });
    expect(toggle).toBeDisabled();
    const row = toggle.closest("tr") as HTMLElement;
    expect(within(row).getAllByText("Alleen lezen").length).toBeGreaterThan(0);
  });

  it("members list their departments", async () => {
    server.use(...world());
    renderApp(`/w/${workspace.id}/leden`);
    const table = await screen.findByRole("region", { name: "Leden" });
    expect(
      await within(table).findByRole("columnheader", { name: "Afdelingen" }),
    ).toBeInTheDocument();
    const row = within(table).getByRole("rowheader", { name: /Bram/ }).closest("tr");
    expect(await within(row as HTMLElement).findByText("Finance")).toBeInTheDocument();
  });

  it("no sorter column while everyone sees all post", async () => {
    server.use(...world({ shown: { ...ownPost, everyone_sees_all: true } }));
    renderApp(`/w/${workspace.id}/leden`);
    await screen.findByRole("rowheader", { name: /Bram/ });
    expect(screen.queryByRole("columnheader", { name: "Verdeelt post" })).toBeNull();
  });

  it("promoting a member while admins read all asks for confirmation", async () => {
    const seen: { confirmed: boolean; preview_token: string }[] = [];
    server.use(
      http.put(`${base}/members/${bram}/role`, confirmFirst("role", seen)),
      gains("Bram", "alle post als beheerder", "role-token"),
      ...world({ shown: { ...ownPost, admins_read_all: true } }),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/leden`);
    await u.click(await screen.findByRole("button", { name: "Rol wijzigen: Bram" }));
    const roleDialog = await screen.findByRole("dialog", { name: "Rol van Bram" });
    await u.click(within(roleDialog).getByRole("radio", { name: "Beheerder" }));
    await u.click(within(roleDialog).getByRole("button", { name: "Opslaan" }));
    const preview = await screen.findByRole("alertdialog");
    await u.click(within(preview).getByRole("button", { name: "Ja, maak Bram Beheerder" }));
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[1]).toMatchObject({ role: "admin", confirmed: true, preview_token: "role-token" });
  });

  it("an invitation for a person asks through the preview", async () => {
    const seen: Record<string, unknown>[] = [];
    let previewed: unknown = null;
    server.use(
      http.post(`${base}/invitations`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        seen.push(body);
        if (!body.confirmed) {
          return HttpResponse.json(
            errorBody(
              "confirmation_required",
              "Bevestig eerst dat deze mensen meer post gaan zien.",
            ),
            { status: 409 },
          );
        }
        return HttpResponse.json(
          {
            link: "https://brievie.local/uitnodiging#abc",
            expires_at: "2099-01-01T00:00:00Z",
            home_network_only: false,
          },
          { status: 201 },
        );
      }),
      http.post(`${base}/visibility/preview`, async ({ request }) => {
        previewed = await request.json();
        return HttpResponse.json({
          changes: [
            {
              user_id: "00000000-0000-0000-0000-000000000000",
              name: "Nieuw lid",
              gains: ["post van Papa"],
              losses: [],
            },
          ],
          letter_count: 2,
          needs_confirmation: true,
          sentences: [],
          preview_token: "invite-token",
        });
      }),
      ...world(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/leden`);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog", { name: "Iemand uitnodigen" });
    await u.selectOptions(within(dialog).getByRole("combobox", { name: "Wie is het?" }), "Papa");
    await u.click(within(dialog).getByRole("button", { name: "Link maken" }));
    const preview = await screen.findByRole("alertdialog");
    await u.click(within(preview).getByRole("button", { name: "Ja, maak de uitnodiging" }));
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(previewed).toMatchObject({
      member_change: "invite",
      member_role: "member",
      member_person_id: papa.id,
    });
    expect(seen[1]).toMatchObject({
      person_id: papa.id,
      confirmed: true,
      preview_token: "invite-token",
    });
  });
});

describe("Uitnodigen", () => {
  it("offers persons but never a department", async () => {
    server.use(...world());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/leden`);
    await u.click(await screen.findByRole("button", { name: "Uitnodigen" }));
    const dialog = await screen.findByRole("dialog", { name: "Iemand uitnodigen" });
    const who = await within(dialog).findByRole("combobox", { name: "Wie is het?" });
    const options = within(who)
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(options).toContain("Papa");
    expect(options).not.toContain("Finance");
  });
});

describe("Nieuwe link", () => {
  it("renewing an admin invitation asks again when it widens now", async () => {
    const invitation = {
      id: "f9999999-9999-4999-8999-999999999999",
      role: "admin",
      email: "nina@example.nl",
      note: "",
      person_id: null,
      person_name: "",
      created_by_name: "Anna",
      created_at: "2026-11-20T10:00:00Z",
      expires_at: "2026-11-27T10:00:00Z",
      status: "open",
    };
    const seen: Record<string, unknown>[] = [];
    server.use(
      http.get(`${base}/invitations`, () => HttpResponse.json([invitation])),
      http.post(`${base}/invitations/${invitation.id}/renew`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        seen.push(body);
        if (!body.confirmed) {
          return HttpResponse.json(
            errorBody(
              "confirmation_required",
              "Bevestig eerst dat deze mensen meer post gaan zien.",
            ),
            { status: 409 },
          );
        }
        return HttpResponse.json(
          { link: "https://brievie.local/uitnodiging#new", home_network_only: false, invitation },
          { status: 201 },
        );
      }),
      gains("Nieuwe beheerder", "alle post als beheerder", "renew-token"),
      ...world(),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/leden`);
    await u.click(await screen.findByRole("button", { name: "Nieuwe link: nina@example.nl" }));
    const preview = await screen.findByRole("alertdialog");
    await u.click(within(preview).getByRole("button", { name: "Ja, maak een nieuwe link" }));
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen[1]).toEqual({ confirmed: true, preview_token: "renew-token" });
  });
});

describe("Automatisering", () => {
  it("a department rule says where the post goes while departments are off", async () => {
    const departmentRule = {
      ...apkRule,
      actions: [{ type: "add_addressee", person_id: finance.id }],
    };
    server.use(
      http.get(`${base}/rules`, () => HttpResponse.json([departmentRule])),
      http.get(`${base}/rules/templates`, () => HttpResponse.json([])),
      ...labelHandlers(),
      ...world({ shown: { ...ownPost, departments_enabled: false } }),
    );
    renderApp(`/w/${workspace.id}/automatisering`);
    expect(
      await screen.findByText("Afdelingen staan uit: deze post gaat nu naar wie post verdeelt."),
    ).toBeInTheDocument();
  });
});
