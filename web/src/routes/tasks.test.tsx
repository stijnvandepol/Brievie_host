import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  asRole,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

type Item = components["schemas"]["TaskItemOut"];
type MyTasks = components["schemas"]["MyTasksOut"];

const base = `*${API}/workspaces/${workspace.id}`;
const path = `/w/${workspace.id}/taken`;

let counter = 0;
function task(overrides: Partial<Item> = {}): Item {
  counter += 1;
  return {
    id: `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`,
    type: "pay",
    description: `Taak ${counter}`,
    deadline: null,
    deadline_source: "manual",
    status: "open",
    completed_at: null,
    document_id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d",
    sender: "Belastingdienst",
    updated_at: "2026-11-20T10:00:00Z",
    created_by_ai: false,
    deadline_quote: "",
    term_quote: "",
    needs_deadline: false,
    amount: "",
    days_left: null,
    group: "no_date",
    reason: "addressee",
    for_names: [],
    assigned_to_id: null,
    assigned_to_name: "",
    assigned_by_name: "",
    completed_by_name: "",
    document_type: "assessment",
    can_edit: true,
    can_take: true,
    can_hand_over: true,
    can_release: false,
    wait_over: false,
    ...overrides,
  };
}

function answer(items: Item[], extra: Partial<MyTasks> = {}): MyTasks {
  return {
    items,
    today: "2026-11-21",
    has_person: true,
    truncated: false,
    waiting_letters: 0,
    split: false,
    ...extra,
  };
}

/** Answers each part and status from its own list. */
function lists(byKey: Record<string, MyTasks>, seen: string[] = []) {
  return http.get(`${base}/my-tasks`, ({ request }) => {
    const query = new URL(request.url).searchParams;
    const key = `${query.get("part") ?? "mine"}/${query.get("status") ?? "open"}`;
    seen.push(key);
    return HttpResponse.json(byKey[key] ?? answer([]));
  });
}

describe("Mijn taken", () => {
  it("groups in order Te laat, Binnen 7 dagen, Later, Zonder datum with counts", async () => {
    const items = [
      task({ description: "Te laat 1", group: "overdue", days_left: -2, deadline: "2026-11-19" }),
      task({ description: "Te laat 2", group: "overdue", days_left: -1, deadline: "2026-11-20" }),
      task({ description: "Bijna", group: "within_week", days_left: 3, deadline: "2026-11-24" }),
      task({ description: "Ooit", group: "later", days_left: 30, deadline: "2026-12-21" }),
      task({ description: "Geen datum", group: "no_date" }),
    ];
    server.use(...loggedInHandlers(), lists({ "mine/open": answer(items) }));
    renderApp(path);
    const headings = await screen.findAllByRole("heading", { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual([
      "Te laat (2)",
      "Binnen 7 dagen (1)",
      "Later (1)",
      "Zonder datum (1)",
    ]);
    const late = screen.getByRole("region", { name: "Te laat" });
    expect(within(late).getAllByRole("listitem")).toHaveLength(2);
  });

  it("a row shows action, days left, amount, sender, for whom and the quote", async () => {
    const item = task({
      description: "Betaal de aanslag",
      group: "within_week",
      days_left: 5,
      deadline: "2026-11-26",
      deadline_source: "in_document",
      deadline_quote: "te betalen vóór 26 november 2026",
      amount: "1234.56",
      for_names: ["Piet"],
    });
    server.use(...loggedInHandlers(), lists({ "mine/open": answer([item]) }));
    renderApp(path);
    const row = (await screen.findByRole("checkbox", { name: "Klaar: Betaal de aanslag" })).closest(
      "li",
    ) as HTMLElement;
    expect(within(row).getByText("Nog 5 dagen (26 nov)")).toBeVisible();
    expect(within(row).getByText(/1\.234,56/)).toBeVisible();
    expect(within(row).getByText("Van Belastingdienst · Voor jou en Piet")).toBeVisible();
    expect(within(row).getByText("Uit de brief: ‘te betalen vóór 26 november 2026’")).toBeVisible();
    expect(
      within(row).getByRole("link", { name: "Brief bekijken: Betaal de aanslag" }),
    ).toHaveAttribute("href", `/w/${workspace.id}/post/${item.document_id}`);
  });

  it("Datum nodig rows come first under Zonder datum", async () => {
    const needs = task({
      description: "Maak bezwaar",
      needs_deadline: true,
      term_quote: "binnen zes weken na dagtekening",
    });
    const plain = task({ description: "Bel terug" });
    server.use(...loggedInHandlers(), lists({ "mine/open": answer([needs, plain]) }));
    renderApp(path);
    const group = await screen.findByRole("region", { name: "Zonder datum" });
    const [first, second] = within(group).getAllByRole("listitem");
    expect(within(first as HTMLElement).getByText("Datum nodig")).toBeVisible();
    expect(
      within(first as HTMLElement).getByText(
        "De brief noemt een termijn: ‘binnen zes weken na dagtekening’. Vul de datum in.",
      ),
    ).toBeVisible();
    expect(within(second as HTMLElement).queryByText("Datum nodig")).not.toBeInTheDocument();
  });

  it("ticking keeps the row with Ongedaan maken and announces it", async () => {
    const item = task({ description: "Betaal de aanslag" });
    const posts: string[] = [];
    server.use(
      ...loggedInHandlers(),
      lists({ "mine/open": answer([item]) }),
      http.post(`${base}/tasks/${item.id}/:action`, ({ params }) => {
        posts.push(String(params.action));
        return HttpResponse.json({
          ...item,
          status: params.action === "complete" ? "done" : "open",
        });
      }),
    );
    const clicker = userEvent.setup();
    renderApp(path);
    const box = await screen.findByRole("checkbox", { name: "Klaar: Betaal de aanslag" });
    await clicker.click(box);
    await waitFor(() => expect(posts).toEqual(["complete"]));
    expect(await screen.findByRole("status")).toHaveTextContent("Afgevinkt: Betaal de aanslag");
    expect(screen.getByRole("checkbox", { name: "Klaar: Betaal de aanslag" })).toBeChecked();
    await clicker.click(await screen.findByRole("button", { name: "Ongedaan maken" }));
    await waitFor(() => expect(posts).toEqual(["complete", "reopen"]));
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Klaar: Betaal de aanslag" })).not.toBeChecked(),
    );
  });

  it("Afgerond lists who ticked off and Toch niet klaar reopens", async () => {
    const done = task({
      description: "Betaal de aanslag",
      status: "done",
      completed_at: "2026-11-20T10:00:00Z",
      completed_by_name: "Anne",
      group: "",
    });
    const posts: string[] = [];
    server.use(
      ...loggedInHandlers(),
      lists({ "mine/done": answer([done]) }),
      http.post(`${base}/tasks/${done.id}/reopen`, () => {
        posts.push("reopen");
        return HttpResponse.json({ ...done, status: "open" });
      }),
    );
    const clicker = userEvent.setup();
    renderApp(path);
    await clicker.click(await screen.findByRole("tab", { name: "Afgerond" }));
    expect(await screen.findByText("Afgevinkt door Anne op 20 nov")).toBeVisible();
    await clicker.click(screen.getByRole("button", { name: "Toch niet klaar: Betaal de aanslag" }));
    await waitFor(() => expect(posts).toEqual(["reopen"]));
  });

  it("Alle taken shows tasks of others with Voor Piet", async () => {
    const pieters = task({ description: "Verleng de pas", reason: "other", for_names: ["Piet"] });
    const seen: string[] = [];
    server.use(...loggedInHandlers(), lists({ "all/open": answer([pieters]) }, seen));
    const clicker = userEvent.setup();
    renderApp(path);
    await screen.findByText(
      "Je hebt geen open taken. Nieuwe taken komen hier vanzelf zodra je post is nagekeken.",
    );
    await clicker.click(screen.getByRole("radio", { name: "Alle taken" }));
    expect(await screen.findByText("Van Belastingdienst · Voor Piet")).toBeVisible();
    expect(seen).toContain("all/open");
  });

  it("an organisation shows Op te pakken with Ik doe dit", async () => {
    const finance = task({
      description: "Betaal de leverancier",
      reason: "department",
      for_names: ["Finance"],
    });
    const assigned: unknown[] = [];
    server.use(
      asRole("member", "business"),
      ...loggedInHandlers(),
      lists({
        "mine/open": answer([], { split: true }),
        "to_pick_up/open": answer([finance], { split: true }),
      }),
      http.post(`${base}/tasks/${finance.id}/assign`, async ({ request }) => {
        assigned.push(await request.json());
        return HttpResponse.json({ ...finance, reason: "assigned" });
      }),
    );
    const clicker = userEvent.setup();
    renderApp(path);
    const section = await screen.findByRole("region", { name: "Op te pakken" });
    expect(await within(section).findByText("Van Belastingdienst · Voor Finance")).toBeVisible();
    expect(within(section).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Op te pakken (1)",
    );
    await clicker.click(
      within(section).getByRole("button", { name: "Ik doe dit: Betaal de leverancier" }),
    );
    await waitFor(() => expect(assigned).toEqual([{ user_id: user.id }]));
  });

  it("empty, no-person and viewer states say what to do", async () => {
    server.use(
      asRole("viewer"),
      ...loggedInHandlers(),
      lists({ "mine/open": answer([task({ description: "Bekijk dit" })], { has_person: false }) }),
    );
    renderApp(path);
    expect(
      await screen.findByText(
        "Je account hangt nog niet aan een naam. Daarom zie je hier alleen taken die iemand aan je geeft. Vraag een beheerder om je te koppelen bij Mensen.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Je kunt taken bekijken, maar niet afvinken.")).toBeVisible();
    expect(await screen.findByRole("checkbox", { name: "Klaar: Bekijk dit" })).toBeDisabled();
  });

  it("the waiting-letters line links to Inbox", async () => {
    server.use(...loggedInHandlers(), lists({ "mine/open": answer([], { waiting_letters: 3 }) }));
    renderApp(path);
    expect(
      await screen.findByText(
        /Er wachten nog 3 brieven op nakijken\. Daar kunnen nieuwe taken uit komen\./,
      ),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Naar Inbox" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/inbox`,
    );
  });
});
