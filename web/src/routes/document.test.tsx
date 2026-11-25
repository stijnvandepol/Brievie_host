import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { addDays, amsterdamToday } from "../task-time";
import {
  API,
  annaPerson,
  asRole,
  belasting,
  documentDetail,
  documentHandlers,
  documentSummary,
  gate,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  renderApp,
  sales,
  server,
  trashedLetter,
  user,
  workspace,
} from "../test-utils";

const task = {
  id: "0f0e0d0c-0b0a-4908-8706-050403020101",
  type: "pay",
  description: "Betaal de aanslag",
  deadline: "2026-12-01",
  deadline_quote: "uiterlijk 1 december",
  deadline_source: "letter",
  status: "open",
  completed_at: null,
  created_by_ai: false,
  amount: "",
  term_quote: "",
  assigned_to_name: "",
  assigned_to_id: null,
  can_take: true,
  can_hand_over: true,
  can_release: false,
  wait_over: false,
};
const path = `/w/${workspace.id}/post/${documentSummary.id}`;

describe("document", () => {
  it("shows one image per page with alt text", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    const second = await screen.findByRole("img", { name: "Pagina 2 van 3" });
    expect(screen.getAllByRole("img")).toHaveLength(3);
    expect(second.getAttribute("src")).toMatch(
      new RegExp(`/api/v1/workspaces/${workspace.id}/documents/${documentSummary.id}/pages/2$`),
    );
    expect(second).toHaveAttribute("loading", "lazy");
  });

  it("reserves the page's space with a class, not an inline style", async () => {
    // jsdom has no layout: the A4 aspect ratio lives in `.document-page` in styles.css.
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    const image = await screen.findByRole("img", { name: "Pagina 1 van 3" });
    expect(image).toHaveClass("document-page");
    expect(image).not.toHaveAttribute("style");
  });

  it("always has a level 1 heading: while loading, on an error and once loaded", async () => {
    const answer = gate();
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, async () => {
        await answer.opened;
        return HttpResponse.json(documentDetail);
      }),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Brief" })).toBeInTheDocument();
    answer.open();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Belastingdienst" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("keeps the heading when the document cannot be found", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, () =>
        HttpResponse.json(
          { code: "not_found", message: "Deze brief bestaat niet." },
          { status: 404 },
        ),
      ),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByRole("alert")).toHaveTextContent("Deze brief bestaat niet.");
    expect(screen.getByRole("heading", { level: 1, name: "Brief" })).toBeInTheDocument();
  });

  it("names the read-only AI fields 'Wat de AI las'", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        status: "needs_review",
        proposals: [
          {
            confidence: "0.900",
            field: "sender",
            group: 0,
            model: "m",
            needs_attention: false,
            provider: "p",
            quote: "Belastingdienst Utrecht",
            value: "Belastingdienst",
          },
        ],
      }),
    );
    renderApp(path);
    expect(await screen.findByRole("heading", { name: "Wat de AI las" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Voorstel van de AI" })).not.toBeInTheDocument();
  });

  it("shows sender, summary, tasks and the letter text", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Belastingdienst" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Je krijgt een aanslag van 120 euro.")).toBeInTheDocument();
    expect(screen.getByText("Betaal de aanslag")).toBeInTheDocument();
    expect(screen.getByText("Tekst van de brief")).toBeInTheDocument();
  });

  it("ticks a task off and opens it again", async () => {
    const posts: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/tasks/${task.id}/:action`, ({ params }) => {
        posts.push(String(params.action));
        return HttpResponse.json({});
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    const box = await screen.findByRole("checkbox", { name: "Betaal de aanslag" });
    expect(box).not.toBeChecked();
    await user.click(box);
    await waitFor(() => expect(posts).toEqual(["complete"]));
  });

  it("shows a done task struck through with its completion date", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        tasks: [{ ...task, status: "done", completed_at: "2026-11-12T10:00:00Z" }],
      }),
    );
    const user = userEvent.setup();
    const posts: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/tasks/${task.id}/:action`, ({ params }) => {
        posts.push(String(params.action));
        return HttpResponse.json({});
      }),
    );
    renderApp(path);
    const box = await screen.findByRole("checkbox", { name: "Betaal de aanslag" });
    expect(box).toBeChecked();
    expect(screen.getByText(/Afgerond op 12 nov/)).toBeInTheDocument();
    await user.click(box);
    await waitFor(() => expect(posts).toEqual(["reopen"]));
  });

  it("disables the task checkbox for a viewer", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByRole("checkbox", { name: "Betaal de aanslag" })).toBeDisabled();
  });

  it("marks an AI summary", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    expect(
      await screen.findByText("Gemaakt door AI. Controleer of het klopt."),
    ).toBeInTheDocument();
  });

  it("does not mark a summary that is not from AI", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({ ...documentDetail, summary_by_ai: false }),
    );
    renderApp(path);
    await screen.findByText("Je krijgt een aanslag van 120 euro.");
    expect(screen.queryByText(/Gemaakt door AI/)).not.toBeInTheDocument();
  });

  it("points a document in review to the app and shows the proposals read-only", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        status: "needs_review",
        proposals: [
          {
            confidence: "0.900",
            field: "sender",
            group: 0,
            model: "m",
            needs_attention: false,
            provider: "p",
            quote: "Belastingdienst Utrecht",
            value: "Belastingdienst",
          },
        ],
      }),
    );
    renderApp(path);
    expect(await screen.findByText(/Controleer deze brief in de app\./)).toBeInTheDocument();
    expect(screen.getByText("Belastingdienst Utrecht")).toBeInTheDocument();
  });

  it("says a document in processing is still being read", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({ ...documentDetail, status: "processing" }),
    );
    renderApp(path);
    expect(await screen.findByText("Deze brief wordt nog gelezen.")).toBeInTheDocument();
  });

  it("sends the new set of addressees from the picker", async () => {
    const puts: unknown[] = [];
    server.use(
      http.put(
        `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/addressees`,
        async ({ request }) => {
          puts.push(await request.json());
          return HttpResponse.json({ ...documentDetail, addressee_ids: [papa.id, annaPerson.id] });
        },
      ),
      ...loggedInHandlers(),
      ...documentHandlers({ ...documentDetail, addressee_ids: [papa.id] }),
    );
    const user = userEvent.setup();
    renderApp(path);
    const picker = await screen.findByRole("button", { name: /Op naam van/ });
    await waitFor(() => expect(picker).toHaveTextContent("Papa"));
    await user.click(picker);
    await user.click(await screen.findByRole("option", { name: "Anna" }));
    await waitFor(() => expect(puts).toEqual([{ person_ids: [papa.id, annaPerson.id] }]));
  });

  it("sets the labels of the letter through the picker", async () => {
    const puts: unknown[] = [];
    server.use(
      http.put(
        `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/labels`,
        async ({ request }) => {
          puts.push(await request.json());
          return HttpResponse.json({ ...documentDetail, label_ids: [belasting.id, sales.id] });
        },
      ),
      ...loggedInHandlers(),
      ...documentHandlers({ ...documentDetail, label_ids: [belasting.id] }),
    );
    const user = userEvent.setup();
    renderApp(path);
    const picker = await screen.findByRole("button", { name: /Labels/ });
    await waitFor(() => expect(picker).toHaveTextContent("Belastingdienst"));
    await user.click(picker);
    await user.click(await screen.findByRole("option", { name: "Sales" }));
    await waitFor(() => expect(puts).toEqual([{ label_ids: [belasting.id, sales.id] }]));
  });

  it("shows the label picker disabled to a viewer", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByRole("button", { name: /Labels/ })).toBeDisabled();
  });

  it("shows the picker disabled to a viewer", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByRole("button", { name: /Op naam van/ })).toBeDisabled();
  });

  it("shows the picker disabled while the letter is being read", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({ ...documentDetail, status: "processing" }),
    );
    renderApp(path);
    expect(await screen.findByRole("button", { name: /Op naam van/ })).toBeDisabled();
  });

  it("lists the history as sentences and skips a kind it does not know", async () => {
    const event = {
      can_undo: false,
      undone: false,
      label_name: "",
      rule_name: "",
      actor_name: "Anna",
      origin: "user",
    };
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`, () =>
        HttpResponse.json([
          {
            ...event,
            id: "e1",
            kind: "addressee_added",
            person_name: "Papa",
            created_at: "2026-11-10T10:00:00Z",
          },
          {
            ...event,
            id: "e2",
            kind: "addressee_removed",
            actor_name: "Bram",
            person_name: "Papa",
            created_at: "2026-11-10T11:00:00Z",
          },
          {
            ...event,
            id: "e3",
            kind: "confirmed",
            person_name: "",
            created_at: "2026-11-10T09:45:00Z",
          },
          {
            ...event,
            id: "e5",
            kind: "task_completed",
            person_name: "",
            created_at: "2026-11-10T12:10:00Z",
          },
          {
            ...event,
            id: "e6",
            kind: "task_reopened",
            person_name: "",
            created_at: "2026-11-10T12:20:00Z",
          },
          {
            ...event,
            id: "e4",
            kind: "from_the_future",
            person_name: "",
            created_at: "2026-11-10T12:00:00Z",
          },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Jij zette deze brief op naam van Papa")).toBeInTheDocument();
    expect(screen.getByText("Bram haalde Papa weg")).toBeInTheDocument();
    expect(screen.getByText("Jij bevestigde deze brief")).toBeInTheDocument();
    expect(screen.getByText("Jij rondde een taak af")).toBeInTheDocument();
    expect(screen.getByText("Jij zette een taak weer open")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").filter((li) => li.closest("ol"))).toHaveLength(5);
  });

  it("says Jij by the actor's id, not by the name", async () => {
    const event = {
      can_undo: false,
      undone: false,
      label_name: "",
      rule_name: "",
      origin: "user",
      person_name: "",
    };
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`, () =>
        HttpResponse.json([
          {
            ...event,
            id: "e1",
            kind: "confirmed",
            actor_id: "11111111-2222-4333-8444-555555555555",
            actor_name: user.name,
            created_at: "2026-11-10T10:00:00Z",
          },
          {
            ...event,
            id: "e2",
            kind: "task_completed",
            actor_id: user.id,
            actor_name: "Anna de Vries",
            created_at: "2026-11-10T11:00:00Z",
          },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Jij rondde een taak af")).toBeInTheDocument();
    expect(screen.getByText(`${user.name} bevestigde deze brief`)).toBeInTheDocument();
  });

  it("names the trash and the restore in the history", async () => {
    const base = {
      can_undo: false,
      undone: false,
      label_name: "",
      rule_name: "",
      person_name: "",
      origin: "user",
      created_at: "2026-11-10T10:00:00Z",
    };
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`, () =>
        HttpResponse.json([
          { ...base, id: "e1", kind: "trashed", actor_id: "b3", actor_name: "Bram" },
          { ...base, id: "e2", kind: "restored", actor_id: user.id, actor_name: user.name },
          { ...base, id: "e3", kind: "trashed", actor_id: null, actor_name: "" },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Naar de prullenbak verplaatst door Bram")).toBeInTheDocument();
    expect(screen.getByText("Teruggezet uit de prullenbak door jou")).toBeInTheDocument();
    expect(screen.getByText("Naar de prullenbak verplaatst door Brievie")).toBeInTheDocument();
  });

  const eventsUrl = `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`;
  const ruleEvent = {
    id: "e9",
    kind: "label_added",
    origin: "rule",
    actor_name: "",
    rule_name: "APK",
    label_name: "Auto",
    person_name: "",
    can_undo: true,
    undone: false,
    created_at: "2026-11-10T10:00:00Z",
  };

  it("undoes a change a rule made from the history", async () => {
    const undone: string[] = [];
    let events = [ruleEvent];
    server.use(
      http.get(eventsUrl, () => HttpResponse.json(events)),
      http.post(`${eventsUrl}/:eventId/undo`, ({ params }) => {
        undone.push(String(params.eventId));
        events = [
          {
            ...ruleEvent,
            id: "e10",
            kind: "label_removed",
            origin: "undo",
            actor_name: "Anna",
            rule_name: "",
            can_undo: false,
            created_at: "2026-11-10T11:00:00Z",
          },
          { ...ruleEvent, can_undo: false, undone: true },
        ];
        return HttpResponse.json(documentDetail);
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Regel APK zette het label Auto erop")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "Terugdraaien: Regel APK zette het label Auto erop" }),
    );

    await waitFor(() => expect(undone).toEqual(["e9"]));
    expect(await screen.findByText("Teruggedraaid")).toBeInTheDocument();
    expect(screen.getByText("Jij haalde het label Auto eraf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Terugdraaien/ })).not.toBeInTheDocument();
  });

  it("shows a viewer no undo button", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      http.get(eventsUrl, () => HttpResponse.json([ruleEvent])),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Regel APK zette het label Auto erop")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Terugdraaien/ })).not.toBeInTheDocument();
  });
});

describe("document made by the autopilot", () => {
  const eventsUrl = `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`;
  const base = {
    can_undo: false,
    undone: false,
    label_name: "",
    rule_name: "",
    person_name: "",
    actor_name: "",
    origin: "ai",
  };
  const confirmedByAi = { ...documentDetail, confirmed_by_ai: true, status: "done" };

  it("shows no badge on a letter a person handled", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    await screen.findByText("Betaal de aanslag");
    expect(screen.queryByText("Automatisch door AI")).not.toBeInTheDocument();
  });

  it("shows the badge when the AI confirmed the letter", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers(confirmedByAi));
    renderApp(path);
    expect(await screen.findByText("Automatisch door AI")).toBeVisible();
  });

  it("keeps the badge and marks what is automatic after the letter was reopened", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        status: "needs_review",
        confirmed_by_ai: false,
        label_ids: [belasting.id, sales.id],
        auto_label_ids: [belasting.id],
        addressee_ids: [papa.id],
        auto_addressee_ids: [papa.id],
      }),
    );
    renderApp(path);
    expect(await screen.findByText("Automatisch door AI")).toBeVisible();
    expect(await screen.findByText("Automatisch toegevoegd: Belastingdienst")).toBeVisible();
    expect(await screen.findByText("Automatisch toegevoegd: Papa")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Zelf controleren" })).not.toBeInTheDocument();
  });

  it("describes the automatic history lines and offers undo", async () => {
    server.use(
      http.get(eventsUrl, () =>
        HttpResponse.json([
          {
            ...base,
            id: "a1",
            kind: "label_added",
            label_name: "Auto",
            can_undo: true,
            created_at: "2026-11-10T10:00:00Z",
          },
          {
            ...base,
            id: "a2",
            kind: "addressee_added",
            origin: "name_match",
            person_name: "Papa",
            can_undo: true,
            created_at: "2026-11-10T10:01:00Z",
          },
          { ...base, id: "a3", kind: "confirmed", created_at: "2026-11-10T10:02:00Z" },
          {
            ...base,
            id: "a5",
            kind: "label_added",
            origin: "rule_autopilot",
            rule_name: "Label Gemeente",
            label_name: "Gemeente",
            can_undo: false,
            created_at: "2026-11-10T10:02:30Z",
          },
          {
            ...base,
            id: "a4",
            kind: "reopened",
            origin: "user",
            actor_name: "Bram",
            created_at: "2026-11-10T10:03:00Z",
          },
        ]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(
      await screen.findByText("Automatisch door AI toegevoegd: label Auto"),
    ).toBeInTheDocument();
    expect(screen.getByText("Automatisch toegevoegd: naam herkend (Papa)")).toBeInTheDocument();
    expect(screen.getByText("Automatisch bevestigd door AI")).toBeInTheDocument();
    expect(
      screen.getByText("Automatisch toegevoegd door regel Label Gemeente: label Gemeente"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Bram zette de brief terug in de Inbox om te controleren"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Terugdraaien:/ })).toHaveLength(2);
  });

  it("reopens a letter the AI confirmed, after a question", async () => {
    const reopened: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/reopen`, () => {
        reopened.push(documentSummary.id);
        return HttpResponse.json({
          ...confirmedByAi,
          status: "needs_review",
          confirmed_by_ai: false,
        });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(confirmedByAi),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Zelf controleren" }));

    const dialog = await screen.findByRole("alertdialog");
    expect(
      within(dialog).getByText(
        "De brief komt terug in de Inbox, bij Controleren. De taken die de AI maakte en die nog open staan, verdwijnen. Je controleert de brief daarna in de app.",
      ),
    ).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Annuleren" })).toHaveFocus();
    expect(reopened).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Zelf controleren" }));
    await waitFor(() => expect(reopened).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    // The button is gone with the AI's confirmation: the focus lands on the page heading.
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveFocus());
  });

  it("marks a task the AI made, and only that one", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        tasks: [
          { ...task, created_by_ai: true },
          { ...task, id: "0f0e0d0c-0b0a-4908-8706-050403020102", description: "Bel terug" },
        ],
      }),
    );
    renderApp(path);
    const tasks = await screen.findByRole("region", { name: "Taken" });
    const [byAi, byHand] = await within(tasks).findAllByRole("listitem");
    if (!byAi || !byHand) throw new Error("two tasks expected");
    expect(within(byAi).getByText("Automatisch door AI")).toBeVisible();
    expect(within(byHand).queryByText("Automatisch door AI")).not.toBeInTheDocument();
  });

  it("asks before Verwijderen, trashes and offers undo on Alle post", async () => {
    const trashed: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/trash`, () => {
        trashed.push(documentSummary.id);
        return HttpResponse.json(trashedLetter());
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Verwijderen" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Brief verwijderen?" });
    expect(
      within(dialog).getByText(
        "De brief gaat naar de prullenbak. Daar blijft hij 30 dagen. Zo lang kun je hem terugzetten. Daarna wordt hij voorgoed gewist.",
      ),
    ).toBeVisible();
    expect(trashed).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Naar de prullenbak" }));
    await waitFor(() => expect(trashed).toHaveLength(1));
    expect(await screen.findByRole("heading", { level: 1, name: "Archief" })).toBeVisible();
    const bar = await screen.findByRole("status");
    expect(bar).toHaveTextContent("De brief staat in de prullenbak.");
    expect(within(bar).getByRole("button", { name: "Ongedaan maken" })).toBeVisible();
  });

  it("restores on Ongedaan maken and opens the letter", async () => {
    const restored: string[] = [];
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/trash`, () =>
        HttpResponse.json(trashedLetter()),
      ),
      http.post(
        `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/restore`,
        () => {
          restored.push(documentSummary.id);
          return HttpResponse.json(documentDetail);
        },
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Verwijderen" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Brief verwijderen?" });
    await user.click(within(dialog).getByRole("button", { name: "Naar de prullenbak" }));
    const bar = await screen.findByRole("status");
    await user.click(within(bar).getByRole("button", { name: "Ongedaan maken" }));
    await waitFor(() => expect(restored).toHaveLength(1));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Belastingdienst" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("De brief staat in de prullenbak.")).not.toBeInTheDocument();
  });

  it("offers a viewer no Verwijderen", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    await screen.findByRole("heading", { level: 1, name: "Belastingdienst" });
    expect(screen.queryByRole("button", { name: "Verwijderen" })).not.toBeInTheDocument();
  });

  it("says a letter is in the trash, with a link there", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, () =>
        HttpResponse.json(
          { code: "document_in_trash", message: "Deze brief staat in de prullenbak." },
          { status: 404 },
        ),
      ),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Deze brief staat in de prullenbak.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Naar de prullenbak" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/prullenbak`,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers no reopen to a viewer", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...documentHandlers(confirmedByAi));
    renderApp(path);
    await screen.findByText("Automatisch door AI");
    expect(screen.queryByRole("button", { name: "Zelf controleren" })).not.toBeInTheDocument();
  });
});

describe("a new person the AI proposes", () => {
  const proposal = {
    id: "f5555555-5555-4555-8555-555555555555",
    name: "M. Jansen",
    quote: "Aan M. Jansen",
    confidence: "0.800",
  };
  const withProposal = { ...documentDetail, new_person_suggestions: [proposal] };
  const base = `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/new-person-suggestions/${proposal.id}`;

  it("asks before anybody is made, and makes the person on Toevoegen als persoon", async () => {
    let accepted = false;
    server.use(
      http.post(`${base}/accept`, () => {
        accepted = true;
        return HttpResponse.json({ ...documentDetail, addressee_ids: [papa.id] });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(withProposal),
    );
    const u = userEvent.setup();
    renderApp(path);
    const panel = await screen.findByRole("region", { name: "Nieuwe persoon?" });
    expect(panel).toHaveTextContent(
      "Deze brief is voor M. Jansen. Die persoon staat nog niet in Brievie.",
    );
    expect(panel).toHaveTextContent("Aan M. Jansen");
    expect(accepted).toBe(false);
    await u.click(within(panel).getByRole("button", { name: "Toevoegen als persoon: M. Jansen" }));
    await waitFor(() => expect(accepted).toBe(true));
  });

  it("Negeren sends no to the server", async () => {
    let dismissed = false;
    server.use(
      http.post(`${base}/dismiss`, () => {
        dismissed = true;
        return HttpResponse.json(documentDetail);
      }),
      ...loggedInHandlers(),
      ...documentHandlers(withProposal),
    );
    const u = userEvent.setup();
    renderApp(path);
    const panel = await screen.findByRole("region", { name: "Nieuwe persoon?" });
    await u.click(within(panel).getByRole("button", { name: "Negeren: M. Jansen" }));
    await waitFor(() => expect(dismissed).toBe(true));
  });

  it("a viewer reads the proposal without buttons", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...documentHandlers(withProposal));
    renderApp(path);
    const panel = await screen.findByRole("region", { name: "Nieuwe persoon?" });
    expect(within(panel).queryByRole("button")).toBeNull();
  });
});

describe("read and unread (spec S 5.3)", () => {
  const readUrl = `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/read`;

  it("opening a letter marks it read once, not on a refetch or a list", async () => {
    const calls: string[] = [];
    server.use(
      http.post(readUrl, () => {
        calls.push("read");
        return new HttpResponse(null, { status: 204 });
      }),
      ...loggedInHandlers(),
      ...documentHandlers({ ...documentDetail, unread: true }),
    );
    // A list shows the letter: nothing is marked.
    const list = renderApp(`/w/${workspace.id}/archief`);
    await screen.findByRole("link", { name: "Belastingdienst" });
    expect(calls).toEqual([]);

    await list.router.navigate({
      to: "/w/$workspaceId/post/$documentId",
      params: { workspaceId: workspace.id, documentId: documentSummary.id },
    });
    await screen.findByRole("heading", { level: 1, name: "Belastingdienst" });
    await waitFor(() => expect(calls).toEqual(["read"]));
    // The page's own data is asked again (focus, a change): no second mark.
    await list.queryClient.invalidateQueries({ queryKey: ["workspaces", workspace.id] });
    await screen.findByRole("button", { name: "Markeer als ongelezen" });
    expect(calls).toEqual(["read"]);
  });

  it("Markeer als ongelezen brings the dot back and the count up", async () => {
    let unread = false;
    let menuAsked = 0;
    server.use(
      http.delete(readUrl, () => {
        unread = true;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, () =>
        HttpResponse.json({ ...documentDetail, unread }),
      ),
      http.get(`*${API}/workspaces/${workspace.id}/menu/counts`, () => {
        menuAsked += 1;
        return HttpResponse.json({
          inbox: 0,
          my_post_unread: unread ? 1 : 0,
          my_tasks: 0,
          my_tasks_overdue: 0,
          lingering: 0,
          shows_lingering: false,
          has_person: true,
        });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const u = userEvent.setup();
    renderApp(path);
    await u.click(await screen.findByRole("button", { name: "Markeer als ongelezen" }));
    expect(await screen.findByRole("button", { name: "Markeer als gelezen" })).toBeVisible();
    const nav = screen.getByRole("navigation", { name: "Hoofdmenu" });
    expect(await within(nav).findByRole("link", { name: "Mijn post, 1 ongelezen" })).toBeVisible();
    expect(menuAsked).toBeGreaterThan(1);
  });
});

describe("document: letter status", () => {
  const second = {
    ...task,
    id: "0f0e0d0c-0b0a-4908-8706-050403020102",
    type: "respond",
    description: "Bel terug",
  };
  const open = {
    ...documentDetail,
    letter_status: "open",
    overdue: true,
    open_task_count: 2,
    tasks: [task, second],
  };

  function show(detail: typeof open | typeof documentDetail) {
    server.use(...loggedInHandlers(), ...documentHandlers(detail));
    renderApp(path);
  }

  it("shows the status and Te laat next to it", async () => {
    show(open);
    const panel = await screen.findByRole("region", { name: "Gegevens" });
    expect(within(panel).getByText("Openstaand")).toBeInTheDocument();
    expect(within(panel).getByText("Te laat")).toBeInTheDocument();
    expect(within(panel).queryByText("Klaar")).not.toBeInTheDocument();
  });

  it("offers Brief afhandelen to an editor of an open letter only", async () => {
    show(open);
    expect(await screen.findByRole("button", { name: "Brief afhandelen" })).toBeInTheDocument();
  });

  it.each([
    ["a handled letter", { ...open, letter_status: "handled", tasks: [] }, "admin"],
    ["a viewer", open, "viewer"],
  ] satisfies [string, typeof open, "admin" | "viewer"][])(
    "does not offer Brief afhandelen to %s",
    async (_name, detail, role) => {
      server.use(asRole(role), ...loggedInHandlers(), ...documentHandlers(detail));
      renderApp(path);
      await screen.findByRole("region", { name: "Gegevens" });
      expect(screen.queryByRole("button", { name: "Brief afhandelen" })).not.toBeInTheDocument();
    },
  );

  it("names the tasks, asks for the payment first and sends the ids it showed", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(
        `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/handle`,
        async ({ request }) => {
          bodies.push(await request.json());
          return HttpResponse.json({ ...open, letter_status: "handled", tasks: [] });
        },
      ),
    );
    show(open);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Brief afhandelen" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Brief afhandelen?" });
    expect(within(dialog).getByText("Deze taken worden afgevinkt:")).toBeInTheDocument();
    expect(within(dialog).getByText(/Betaal de aanslag/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Bel terug/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Afhandelen" });
    expect(confirm).toBeDisabled();
    await user.click(
      within(dialog).getByRole("checkbox", { name: "De betaling is gedaan of geregeld" }),
    );
    await user.click(confirm);
    await waitFor(() =>
      expect(bodies).toEqual([{ task_ids: [task.id, second.id], payments_confirmed: true }]),
    );
  });

  it("asks nothing about a payment when there is none", async () => {
    show({ ...open, tasks: [second] });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Brief afhandelen" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Afhandelen" })).toBeEnabled();
  });

  it("shows the server's sentence when the tasks changed meanwhile", async () => {
    server.use(
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/handle`, () =>
        HttpResponse.json(
          {
            code: "tasks_changed",
            message: "De taken van deze brief zijn net veranderd. Bekijk ze opnieuw.",
          },
          { status: 409 },
        ),
      ),
    );
    show({ ...open, tasks: [second] });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Brief afhandelen" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Afhandelen" }));
    expect(
      await within(dialog).findByText(
        "De taken van deze brief zijn net veranderd. Bekijk ze opnieuw.",
      ),
    ).toBeInTheDocument();
  });

  it("asks for a new tick when a payment appeared meanwhile", async () => {
    const extra = {
      ...task,
      id: "0f0e0d0c-0b0a-4908-8706-050403020199",
      description: "Betaal de boete",
    };
    let reads = 0;
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, () => {
        reads += 1;
        return HttpResponse.json(
          reads > 1 ? { ...open, tasks: [task, extra] } : { ...open, tasks: [task] },
        );
      }),
      http.post(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/handle`, () =>
        HttpResponse.json(
          {
            code: "tasks_changed",
            message: "De taken van deze brief zijn net veranderd. Bekijk ze opnieuw.",
          },
          { status: 409 },
        ),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Brief afhandelen" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(
      within(dialog).getByRole("checkbox", { name: "De betaling is gedaan of geregeld" }),
    );
    await user.click(within(dialog).getByRole("button", { name: "Afhandelen" }));
    expect(await within(dialog).findByText(/Betaal de boete/)).toBeInTheDocument();
    expect(
      within(dialog).getByRole("checkbox", { name: "De betaling is gedaan of geregeld" }),
    ).not.toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Afhandelen" })).toBeDisabled();
  });

  it("sets a day to wait for a reply, within what the server accepts", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(
        `*${API}/workspaces/${workspace.id}/tasks/${second.id}/waiting`,
        async ({ request }) => {
          bodies.push(await request.json());
          return HttpResponse.json({ ...second, waiting_until: "2099-01-01" });
        },
      ),
    );
    show(open);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Wacht op reactie: Bel terug" }));
    const dialog = await screen.findByRole("dialog", { name: "Wacht op reactie…" });
    const field = within(dialog).getByLabelText("Tot wanneer wacht je op een reactie?");
    const today = amsterdamToday();
    expect(field).toHaveAttribute("min", addDays(today, 1));
    expect(field).toHaveAttribute("max", addDays(today, 365));
    expect(
      within(dialog).getByText(
        "Op die dag komt de taak terug in Mijn taken. Iedereen die deze brief ziet, ziet dat je wacht.",
      ),
    ).toBeInTheDocument();
    await user.type(field, addDays(today, 5));
    await user.click(within(dialog).getByRole("button", { name: "Opslaan" }));
    await waitFor(() => expect(bodies).toEqual([{ until: addDays(today, 5) }]));
  });

  it("marks a waiting task, keeps its term visible and can stop waiting", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post(
        `*${API}/workspaces/${workspace.id}/tasks/${task.id}/waiting`,
        async ({ request }) => {
          bodies.push(await request.json());
          return HttpResponse.json({ ...task, waiting_until: null });
        },
      ),
    );
    const waiting = { ...task, deadline: "2020-11-01", waiting_until: "2099-12-03" };
    show({ ...open, letter_status: "waiting", overdue: false, tasks: [waiting] });
    const user = userEvent.setup();
    expect(await screen.findByText("Wacht op reactie tot 3 dec")).toBeInTheDocument();
    expect(screen.getByText("termijn was 1 nov")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Niet meer wachten: Betaal de aanslag" }));
    await waitFor(() => expect(bodies).toEqual([{ until: null }]));
  });

  it("marks a task whose waiting day has arrived", async () => {
    const over = { ...task, waiting_until: "2026-11-20", wait_over: true };
    show({ ...open, tasks: [over] });
    expect(await screen.findByText("Wachten voorbij sinds 20 nov")).toBeInTheDocument();
  });

  it("lets a viewer snooze, and nothing else", async () => {
    server.use(asRole("viewer"), ...loggedInHandlers(), ...documentHandlers(open));
    renderApp(path);
    expect(
      await screen.findByRole("button", { name: "Uitstellen: Betaal de aanslag" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Wacht op reactie:/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ik doe dit/ })).not.toBeInTheDocument();
  });
});

describe("the letter's origin (intake)", () => {
  it("offers the original of an uploaded file", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...documentDetail,
        source: "upload",
        has_original: true,
        original_name: "aanslag.pdf",
      }),
    );
    renderApp(path);
    expect(await screen.findByText("Binnengekomen via upload")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Origineel downloaden" });
    expect(link.getAttribute("href")).toBe(
      `/api/v1/workspaces/${workspace.id}/documents/${documentSummary.id}/original`,
    );
  });

  it("has no download for an app scan", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(path);
    await screen.findByRole("img", { name: "Pagina 1 van 3" });
    expect(screen.queryByRole("link", { name: "Origineel downloaden" })).not.toBeInTheDocument();
    expect(screen.queryByText("Binnengekomen via upload")).not.toBeInTheDocument();
  });
});
