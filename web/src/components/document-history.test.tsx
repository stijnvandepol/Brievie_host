import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  user,
  workspace,
} from "../test-utils";

type Event = components["schemas"]["DocumentEventOut"];

const other = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b9999";
const bram = "b3333333-3333-4333-8333-333333333333";

function event(id: number, overrides: Partial<Event>): Event {
  return {
    id: `e0000000-0000-4000-8000-00000000000${id}`,
    created_at: `2026-11-1${id}T09:00:00Z`,
    kind: "merged",
    origin: "user",
    actor_id: bram,
    actor_name: "Bram",
    rule_name: "",
    label_name: "",
    person_name: "",
    can_undo: false,
    undone: false,
    related_document_id: other,
    ...overrides,
  };
}

function withEvents(events: Event[]) {
  return http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`, () =>
    HttpResponse.json(events),
  );
}

const path = `/w/${workspace.id}/post/${documentSummary.id}`;

describe("history of duplicate letters", () => {
  it("history shows the merge lines", async () => {
    server.use(
      withEvents([
        event(1, { kind: "merged" }),
        event(2, { kind: "duplicate_dismissed" }),
        event(3, { kind: "task_moved", origin: "merge" }),
        event(4, { kind: "label_added", origin: "merge", label_name: "Wonen" }),
        event(5, { kind: "task_completed", origin: "merge" }),
        event(6, { kind: "merged", actor_id: user.id, actor_name: "Anna" }),
      ]),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(
      await screen.findByText("Samengevoegd met een dubbele brief door Bram"),
    ).toBeInTheDocument();
    expect(screen.getByText("Gekozen: geen dubbele brief, door Bram")).toBeInTheDocument();
    expect(screen.getByText("Taak verplaatst uit de dubbele brief")).toBeInTheDocument();
    expect(
      screen.getByText("Bram zette het label Wonen erop (uit de dubbele brief)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Bram rondde een taak af (uit de dubbele brief)")).toBeInTheDocument();
    expect(screen.getByText("Samengevoegd met een dubbele brief door jou")).toBeInTheDocument();
  });

  it("the letter that went to the trash says so", async () => {
    server.use(
      withEvents([
        event(1, { kind: "trashed", origin: "merge" }),
        event(2, { kind: "task_moved", origin: "merge" }),
      ]),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(
      await screen.findByText("Als dubbele brief naar de prullenbak verplaatst door Bram"),
    ).toBeInTheDocument();
    expect(screen.getByText("Taak verplaatst naar de dubbele brief")).toBeInTheDocument();
  });

  it("a letter trashed in one merge and kept in a later one says which way each task went", async () => {
    const later = "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b8888";
    server.use(
      withEvents([
        event(4, { kind: "task_moved", origin: "merge", related_document_id: later }),
        event(3, { kind: "merged", related_document_id: later }),
        event(2, { kind: "task_moved", origin: "merge" }),
        event(1, { kind: "trashed", origin: "merge" }),
      ]),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Taak verplaatst uit de dubbele brief")).toBeInTheDocument();
    expect(screen.getByText("Taak verplaatst naar de dubbele brief")).toBeInTheDocument();
  });
});

describe("history of tasks (Mijn taken)", () => {
  it("names who gave, released and dated a task, and a release by Brievie", async () => {
    server.use(
      withEvents([
        event(1, { kind: "task_assigned", person_name: "Piet", related_document_id: null }),
        event(2, { kind: "task_unassigned", person_name: "Piet", related_document_id: null }),
        event(3, { kind: "task_deadline_set", related_document_id: null }),
        event(4, {
          kind: "task_unassigned",
          origin: "system",
          actor_id: null,
          actor_name: "",
          person_name: "Femke",
          related_document_id: null,
        }),
      ]),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Bram gaf een taak aan Piet")).toBeInTheDocument();
    expect(screen.getByText("Bram haalde een taak weg bij Piet")).toBeInTheDocument();
    expect(screen.getByText("Bram vulde de datum van een taak in")).toBeInTheDocument();
    expect(
      screen.getByText("Brievie haalde een taak weg bij Femke: die mag deze brief niet meer lezen"),
    ).toBeInTheDocument();
  });
});
