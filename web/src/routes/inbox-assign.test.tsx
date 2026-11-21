import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  annaPerson,
  asRole,
  documentDetail,
  documentSummary,
  HttpResponse,
  http,
  labelHandlers,
  loggedInHandlers,
  members,
  papa,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;
const inbox = `/w/${workspace.id}/inbox`;

type Summary = components["schemas"]["DocumentSummaryOut"];
type Person = components["schemas"]["PersonOut"];
type Visibility = components["schemas"]["VisibilityOut"];

const finance: Person = {
  id: "e1111111-1111-4111-8111-111111111111",
  display_name: "Finance",
  name_variants: [],
  archived: false,
  document_count: 1,
  user_id: null,
  kind: "department",
  member_user_ids: ["b3333333-3333-4333-8333-333333333333"],
  lead_user_ids: [],
};

const hr: Person = {
  ...finance,
  id: "e2222222-2222-4222-8222-222222222222",
  display_name: "HR",
  member_user_ids: [user.id],
};

const ownPost: Visibility = {
  kind: "business",
  everyone_sees_all: false,
  departments_enabled: true,
  admins_read_all: false,
  sorter_user_ids: [user.id],
  sentences: [],
  summary: [],
};

const unnamed: Summary = { ...documentSummary, sender: "Leverancier" };
const forMe: Summary = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b7777",
  sender: "Belastingdienst",
  status: "needs_review",
  inbox_state: "review",
  addressee_ids: [annaPerson.id],
};
const forHr: Summary = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b8888",
  sender: "Arbodienst",
  status: "needs_review",
  inbox_state: "review",
  addressee_ids: [hr.id],
};

function handlers(shown: Visibility = ownPost, list: Summary[] = [unnamed, forMe, forHr]) {
  return [
    asRole("admin", shown.kind === "home" ? "home" : "business"),
    http.get(`${base}/visibility`, () => HttpResponse.json(shown)),
    http.get(`${base}/persons`, () => HttpResponse.json([papa, annaPerson, finance, hr])),
    http.get(`${base}/members`, () => HttpResponse.json(members)),
    http.get(`${base}/documents`, ({ request }) => {
      const state = new URL(request.url).searchParams.get("inbox_state");
      return HttpResponse.json(list.filter((document) => document.inbox_state === state));
    }),
    http.get(`${base}/inbox/counts`, () =>
      HttpResponse.json({ new: 0, review: 2, unassigned: 1, duplicate: 0 }),
    ),
    ...labelHandlers(),
    ...loggedInHandlers(),
  ];
}

describe("inbox: assigning post", () => {
  it("an unassigned letter reads Naam kiezen without departments and Toewijzen with them", async () => {
    server.use(...handlers({ ...ownPost, departments_enabled: false }));
    renderApp(inbox);
    const row = (await screen.findByRole("link", { name: "Leverancier" })).closest("tr");
    expect(await within(row as HTMLElement).findByText("Naam kiezen")).toBeInTheDocument();
  });

  it("with departments on, an unassigned letter reads Toewijzen", async () => {
    server.use(...handlers());
    renderApp(inbox);
    const row = (await screen.findByRole("link", { name: "Leverancier" })).closest("tr");
    expect(await within(row as HTMLElement).findByText("Toewijzen")).toBeInTheDocument();
    expect(await screen.findByText(/Toewijzen: 1/)).toBeInTheDocument();
  });

  it("chips filter on Voor mij, a department and Zonder naam", async () => {
    server.use(...handlers());
    const u = userEvent.setup();
    renderApp(inbox);
    await screen.findByRole("link", { name: "Leverancier" });
    const chips = await screen.findByRole("radiogroup", { name: "Welke brieven" });
    await u.click(within(chips).getByRole("radio", { name: "Voor mij" }));
    expect(screen.getByRole("link", { name: "Belastingdienst" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Leverancier" })).toBeNull();
    await u.click(within(chips).getByRole("radio", { name: "HR" }));
    expect(screen.getByRole("link", { name: "Arbodienst" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Belastingdienst" })).toBeNull();
    await u.click(within(chips).getByRole("radio", { name: "Zonder naam" }));
    expect(screen.getByRole("link", { name: "Leverancier" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Arbodienst" })).toBeNull();
    // Finance is not Anna's department.
    expect(within(chips).queryByRole("radio", { name: "Finance" })).toBeNull();
  });

  it("no chips while everyone sees all post", async () => {
    server.use(...handlers({ ...ownPost, everyone_sees_all: true }));
    renderApp(inbox);
    await screen.findByRole("link", { name: "Leverancier" });
    expect(screen.queryByRole("radiogroup", { name: "Welke brieven" })).toBeNull();
  });

  it("the picker shows departments first", async () => {
    server.use(...handlers());
    const u = userEvent.setup();
    renderApp(inbox);
    const row = (await screen.findByRole("link", { name: "Leverancier" })).closest("tr");
    await u.click(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ }));
    const listbox = await screen.findByRole("listbox");
    const groups = within(listbox).getAllByRole("group");
    expect(groups[0]).toHaveAccessibleName("Afdelingen");
    expect(
      within(groups[0] as HTMLElement)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Finance", "HR"]);
    expect(groups[1]).toHaveAccessibleName("Personen");
  });

  /** A letter's page whose addressees the test changes; `readable` says what the server
   * answers about the caller's sight after a change. */
  function letterPage(letter: Summary, readable: (ids: string[]) => boolean) {
    let current = letter.addressee_ids ?? [];
    const saved: string[][] = [];
    const handlers = [
      http.get(`${base}/documents/${letter.id}`, () =>
        HttpResponse.json({ ...documentDetail, ...letter, addressee_ids: current }),
      ),
      http.get(`${base}/documents/${letter.id}/events`, () => HttpResponse.json([])),
      http.put(`${base}/documents/${letter.id}/addressees`, async ({ request }) => {
        const body = (await request.json()) as { person_ids: string[] };
        current = body.person_ids;
        saved.push(current);
        return HttpResponse.json({
          ...documentDetail,
          ...letter,
          addressee_ids: current,
          can_read: readable(current),
        });
      }),
    ];
    return { handlers, saved };
  }

  it("moving a letter to another department warns before saving", async () => {
    const page = letterPage(forHr, (ids) => ids.includes(hr.id));
    server.use(...page.handlers, ...handlers());
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/post/${forHr.id}`);
    await u.click(await screen.findByRole("button", { name: /Op naam van/ }));
    // Finance next to HR: Anna keeps the letter, saved at once.
    await u.click(await screen.findByRole("option", { name: "Finance" }));
    await waitFor(() => expect(page.saved).toEqual([[hr.id, finance.id]]));
    // HR off: a hand-over, asked before anything is saved.
    await u.click(await screen.findByRole("option", { name: "HR" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(
      within(dialog).getByText("Na toewijzen ziet HR deze brief niet meer. Hij gaat naar Finance."),
    ).toBeInTheDocument();
    expect(page.saved).toHaveLength(1);
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inbox" })).toBeInTheDocument();
    expect(screen.getByText("Je kunt deze brief niet meer openen.")).toBeInTheDocument();
    expect(page.saved[1]).toEqual([finance.id]);
  });

  it("giving a letter to someone else warns and then returns to Inbox", async () => {
    const jan: Person = {
      ...papa,
      id: "a3333333-3333-4333-8333-333333333333",
      display_name: "Jan",
      user_id: "b3333333-3333-4333-8333-333333333333",
    };
    const page = letterPage(forMe, (ids) => ids.includes(annaPerson.id));
    server.use(
      http.get(`${base}/persons`, () => HttpResponse.json([papa, annaPerson, jan, finance, hr])),
      ...page.handlers,
      ...handlers({ ...ownPost, sorter_user_ids: [] }),
    );
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/post/${forMe.id}`);
    await u.click(await screen.findByRole("button", { name: /Op naam van/ }));
    await u.click(await screen.findByRole("option", { name: "Jan" }));
    await waitFor(() => expect(page.saved).toHaveLength(1));
    await u.click(await screen.findByRole("option", { name: "Anna" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(
      within(dialog).getByText("Na opslaan kun je deze brief niet meer zien. Hij gaat naar Jan."),
    ).toBeInTheDocument();
    await u.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Inbox" })).toBeInTheDocument();
    expect(screen.getByText("Je kunt deze brief niet meer openen.")).toBeInTheDocument();
    expect(page.saved[1]).toEqual([jan.id]);
  });

  it("no warning while the caller sees all post", async () => {
    const page = letterPage(forMe, () => true);
    server.use(...page.handlers, ...handlers({ ...ownPost, everyone_sees_all: true }));
    const u = userEvent.setup();
    renderApp(`/w/${workspace.id}/post/${forMe.id}`);
    await u.click(await screen.findByRole("button", { name: /Op naam van/ }));
    await u.click(await screen.findByRole("option", { name: "Papa" }));
    await u.click(await screen.findByRole("option", { name: "Anna" }));
    await waitFor(() => expect(page.saved).toHaveLength(2));
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});
