import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  documentDetail,
  documentHandlers,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

type Item = components["schemas"]["TaskItemOut"];

const base = `*${API}/workspaces/${workspace.id}`;

const needsDate: Item = {
  id: "00000000-0000-4000-8000-0000000000aa",
  type: "object",
  description: "Maak bezwaar",
  deadline: null,
  deadline_source: "manual",
  status: "open",
  completed_at: null,
  document_id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d",
  sender: "Gemeente",
  updated_at: "2026-11-20T10:00:00Z",
  created_by_ai: false,
  deadline_quote: "",
  term_quote: "binnen zes weken na dagtekening",
  needs_deadline: true,
  amount: "",
  days_left: null,
  group: "no_date",
  reason: "addressee",
  for_names: [],
  assigned_to_id: null,
  assigned_to_name: "",
  assigned_by_name: "",
  completed_by_name: "",
  document_type: "decision",
  can_edit: true,
  can_take: true,
  can_hand_over: true,
  can_release: false,
};

function myTasks(items: () => Item[]) {
  return http.get(`${base}/my-tasks`, ({ request }) => {
    const status = new URL(request.url).searchParams.get("status") ?? "open";
    return HttpResponse.json({
      items: status === "open" ? items() : [],
      today: "2026-11-21",
      has_person: true,
      truncated: false,
      waiting_letters: 0,
      split: false,
    });
  });
}

const people = [
  { user_id: user.id, name: "Anna", is_me: true },
  { user_id: "b3333333-3333-4333-8333-333333333333", name: "Bram", is_me: false },
];

describe("task dialogs", () => {
  it("Datum invullen sets a date and the row moves", async () => {
    let current = needsDate;
    const bodies: unknown[] = [];
    server.use(
      ...loggedInHandlers(),
      myTasks(() => [current]),
      http.post(`${base}/tasks/${needsDate.id}/deadline`, async ({ request }) => {
        bodies.push(await request.json());
        current = {
          ...needsDate,
          deadline: "2026-12-15",
          needs_deadline: false,
          group: "later",
          days_left: 24,
        };
        return HttpResponse.json(current);
      }),
    );
    const clicker = userEvent.setup();
    renderApp(`/w/${workspace.id}/taken`);
    await clicker.click(
      await screen.findByRole("button", { name: "Datum invullen: Maak bezwaar" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("De brief noemt een termijn: ‘binnen zes weken na dagtekening’."),
    ).toBeVisible();
    const field = within(dialog).getByLabelText("Welke datum noemt de brief?");
    await clicker.type(field, "2026-12-15");
    await clicker.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(bodies).toEqual([{ deadline: "2026-12-15" }]));
    expect(await screen.findByRole("region", { name: "Later" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Zonder datum" })).not.toBeInTheDocument();
  });

  it("Geef aan lists only the offered members and hands over", async () => {
    const bodies: unknown[] = [];
    server.use(
      ...loggedInHandlers(),
      myTasks(() => [needsDate]),
      http.get(`${base}/tasks/${needsDate.id}/assignees`, () => HttpResponse.json(people)),
      http.post(`${base}/tasks/${needsDate.id}/assign`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...needsDate, assigned_to_name: "Bram" });
      }),
    );
    const clicker = userEvent.setup();
    renderApp(`/w/${workspace.id}/taken`);
    await clicker.click(await screen.findByRole("button", { name: "Geef aan: Maak bezwaar" }));
    const dialog = await screen.findByRole("dialog");
    const group = await within(dialog).findByRole("radiogroup", {
      name: "Aan wie geef je deze taak?",
    });
    expect(
      within(group)
        .getAllByRole("radio")
        .map((r) => r.closest("label")?.textContent),
    ).toEqual(["Anna (jij)", "Bram"]);
    expect(
      within(dialog).getByText("Alleen wie deze brief mag lezen, staat in de lijst."),
    ).toBeVisible();
    await clicker.click(within(group).getByRole("radio", { name: "Bram" }));
    await clicker.click(within(dialog).getByRole("button", { name: "Geef aan" }));
    await waitFor(() =>
      expect(bodies).toEqual([{ user_id: "b3333333-3333-4333-8333-333333333333" }]),
    );
  });

  it("a 422 assignee_cannot_read is shown in Dutch", async () => {
    server.use(
      ...loggedInHandlers(),
      myTasks(() => [needsDate]),
      http.get(`${base}/tasks/${needsDate.id}/assignees`, () => HttpResponse.json(people)),
      http.post(`${base}/tasks/${needsDate.id}/assign`, () =>
        HttpResponse.json(
          errorBody(
            "assignee_cannot_read",
            "Deze persoon mag de brief niet lezen. Kies iemand anders.",
          ),
          { status: 422 },
        ),
      ),
    );
    const clicker = userEvent.setup();
    renderApp(`/w/${workspace.id}/taken`);
    await clicker.click(await screen.findByRole("button", { name: "Geef aan: Maak bezwaar" }));
    const dialog = await screen.findByRole("dialog");
    await clicker.click(await within(dialog).findByRole("radio", { name: "Bram" }));
    await clicker.click(within(dialog).getByRole("button", { name: "Geef aan" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Deze persoon mag de brief niet lezen. Kies iemand anders.",
    );
  });

  it("the letter's task list shows amount and for whom", async () => {
    const [first] = documentDetail.tasks;
    if (!first) throw new Error("a task expected");
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        status: "done",
        tasks: [
          {
            ...first,
            amount: "1234.56",
            assigned_to_name: "Bram",
            assigned_to_id: "b3333333-3333-4333-8333-333333333333",
          },
        ],
      }),
    );
    renderApp(`/w/${workspace.id}/post/${documentDetail.id}`);
    const tasks = await screen.findByRole("region", { name: "Taken" });
    expect(within(tasks).getByText(/1\.234,56/)).toBeVisible();
    expect(within(tasks).getByText("Aan Bram gegeven")).toBeVisible();
    expect(
      within(tasks).getByRole("button", { name: `Ik doe dit: ${first.description}` }),
    ).toBeVisible();
    expect(
      within(tasks).getByRole("button", { name: `Geef aan: ${first.description}` }),
    ).toBeVisible();
  });
});

describe("who may pick up a task (spec S 7)", () => {
  type LetterTask = components["schemas"]["TaskOut"];
  const free: LetterTask = {
    ...(documentDetail.tasks[0] as LetterTask),
    id: "0f0e0d0c-0b0a-4908-8706-050403020201",
    description: "Vrije taak",
    can_take: true,
    can_hand_over: true,
    can_release: false,
  };
  const colleagues: LetterTask = {
    ...free,
    id: "0f0e0d0c-0b0a-4908-8706-050403020202",
    description: "Taak van Bram",
    assigned_to_id: "b3333333-3333-4333-8333-333333333333",
    assigned_to_name: "Bram",
    can_take: false,
    can_hand_over: false,
    can_release: false,
  };
  const letterPath = `/w/${workspace.id}/post/${documentDetail.id}`;

  function letter(tasks: LetterTask[]) {
    let asked = 0;
    server.use(
      http.get(`${base}/documents/${documentDetail.id}`, () => {
        asked += 1;
        return HttpResponse.json({ ...documentDetail, tasks });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    return () => asked;
  }

  it("shows Ik doe dit only on an unassigned task", async () => {
    letter([free, colleagues]);
    renderApp(letterPath);
    expect(await screen.findByRole("button", { name: "Ik doe dit: Vrije taak" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Ik doe dit: Taak van Bram" })).toBeNull();
  });

  it("shows Doet: Bram without buttons on a colleague's task", async () => {
    letter([colleagues]);
    renderApp(letterPath);
    const item = (await screen.findByText("Taak van Bram")).closest("li") as HTMLElement;
    expect(within(item).getByText("Doet: Bram")).toBeVisible();
    expect(
      within(item).queryByRole("button", { name: /Ik doe dit|Geef aan|Vrijgeven/ }),
    ).toBeNull();
  });

  it("an admin sees Vrijgeven and Geef aan… on a colleague's task", async () => {
    letter([{ ...colleagues, can_hand_over: true, can_release: true }]);
    renderApp(letterPath);
    expect(await screen.findByRole("button", { name: "Vrijgeven: Taak van Bram" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Geef aan: Taak van Bram" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Ik doe dit: Taak van Bram" })).toBeNull();
    expect(screen.queryByText("Doet: Bram")).toBeNull();
  });

  it("a 409 task_taken shows the server's sentence and refreshes", async () => {
    const asked = letter([free]);
    const sentence =
      "Iemand anders doet deze taak al. Vraag die persoon of een beheerder om hem door te geven.";
    server.use(
      http.post(`${base}/tasks/${free.id}/assign`, () =>
        HttpResponse.json({ code: "task_taken", message: sentence }, { status: 409 }),
      ),
    );
    const clicker = userEvent.setup();
    renderApp(letterPath);
    await clicker.click(await screen.findByRole("button", { name: "Ik doe dit: Vrije taak" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(sentence);
    // The letter (and so its tasks) is asked again: the page shows who has it now.
    await waitFor(() => expect(asked()).toBeGreaterThan(1));
  });
});
