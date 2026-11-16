import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  annaPerson,
  belasting,
  documentDetail,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  renderApp,
  sales,
  server,
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
    expect(await screen.findByText(/Controleer deze brief in de app/)).toBeInTheDocument();
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
