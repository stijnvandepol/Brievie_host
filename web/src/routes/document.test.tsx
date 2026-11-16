import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  annaPerson,
  documentDetail,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  renderApp,
  server,
  workspace,
} from "../test-utils";

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
    expect(screen.getAllByRole("listitem").filter((li) => li.closest("ol"))).toHaveLength(3);
  });
});
