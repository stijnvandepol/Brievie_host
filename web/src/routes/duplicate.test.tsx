import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  documentDetail,
  documentSummary,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  personHandlers,
  renderApp,
  server,
  trashHandlers,
  workspace,
} from "../test-utils";

type Duplicate = components["schemas"]["DuplicateOut"];

const base = `*${API}/workspaces/${workspace.id}`;
const pairId = "c1111111-1111-4111-8111-111111111111";
const path = `/w/${workspace.id}/dubbel/${pairId}`;

const first = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b1111",
  sender: "Waterschap",
  open_duplicate_count: 1,
};
const second = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b2222",
  sender: "Waterschap",
  page_count: 2,
  created_at: "2026-11-11T09:30:00Z",
  open_duplicate_count: 1,
};

function pairOf(overrides: Partial<Duplicate> = {}): Duplicate {
  return {
    id: pairId,
    state: "open",
    created_at: "2026-11-11T09:31:00Z",
    first,
    second,
    suggested_keep_id: first.id,
    text_similarity: 94,
    same: ["sender", "letter_date"],
    differences: [
      { field: "amount", first: "120.00", second: "180.00", uncertain: false },
      { field: "page_count", first: "3", second: "2", uncertain: true },
    ],
    task_twins: [],
    can_merge: true,
    blocked_reason: "",
    ...overrides,
  };
}

function pairHandlers(pair: Duplicate = pairOf()) {
  return [
    ...loggedInHandlers(),
    ...personHandlers(),
    ...trashHandlers(),
    http.get(`${base}/duplicates/${pairId}`, () => HttpResponse.json(pair)),
    http.get(`${base}/duplicates`, () => HttpResponse.json([pair])),
    http.get(`${base}/documents/${first.id}`, () =>
      HttpResponse.json({ ...documentDetail, ...first }),
    ),
    http.get(`${base}/documents/${second.id}`, () =>
      HttpResponse.json({ ...documentDetail, ...second, tasks: [] }),
    ),
    http.get(`${base}/documents/${first.id}/events`, () => HttpResponse.json([])),
    http.get(`${base}/documents`, () => HttpResponse.json([])),
  ];
}

describe("mogelijk dubbel", () => {
  it("shows both columns and marks Verschilt and Verschilt misschien (slecht leesbaar)", async () => {
    server.use(...pairHandlers());
    renderApp(path);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Mogelijk dubbel" }),
    ).toBeInTheDocument();
    const columns = await screen.findAllByRole("region", { name: /Brief [12]/ });
    expect(columns).toHaveLength(2);
    const [left, right] = columns as [HTMLElement, HTMLElement];
    expect(within(left).getByText("€ 120,00")).toBeInTheDocument();
    expect(within(right).getByText("€ 180,00")).toBeInTheDocument();
    expect(within(left).getAllByText("Verschilt").length).toBeGreaterThan(0);
    expect(within(right).getByText("Verschilt misschien (slecht leesbaar)")).toBeInTheDocument();
    expect(screen.getByText("Lijkt op elkaar voor 94%")).toBeInTheDocument();
  });

  it("warning names the differing fields", async () => {
    server.use(...pairHandlers());
    renderApp(path);
    expect(
      await screen.findByText(
        "Let op: bedrag en pagina's verschilt. Een herinnering of een tweede termijn lijkt vaak op de eerste brief, maar het is een andere brief.",
      ),
    ).toBeInTheDocument();
  });

  it("Samenvoegen sends keep id and complete_twin_tasks true, opens kept letter with undo bar", async () => {
    const bodies: unknown[] = [];
    server.use(
      ...pairHandlers(),
      http.post(`${base}/duplicates/${pairId}/merge`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...documentDetail, ...first });
      }),
    );
    renderApp(path);
    await userEvent.click(await screen.findByRole("button", { name: "Het is dezelfde brief" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Samenvoegen?" });
    expect(within(dialog).getByText(/30 dagen/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Samenvoegen" }));
    expect(await screen.findByText("De brief staat in de prullenbak.")).toBeInTheDocument();
    expect(bodies).toEqual([{ keep_document_id: first.id, complete_twin_tasks: true }]);
    await waitFor(() => expect(document.title).toBe("Waterschap - Brievie"));
  });

  it("keeps the letter the person chose", async () => {
    const bodies: unknown[] = [];
    server.use(
      ...pairHandlers(),
      http.post(`${base}/duplicates/${pairId}/merge`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...documentDetail, ...second });
      }),
      http.get(`${base}/documents/${second.id}/events`, () => HttpResponse.json([])),
    );
    renderApp(path);
    const choices = await screen.findByRole("radiogroup", { name: "Welke brief houd je?" });
    const [keepFirst, keepSecond] = within(choices).getAllByRole("radio") as [
      HTMLElement,
      HTMLElement,
    ];
    expect(keepFirst).toBeChecked();
    await userEvent.click(keepSecond);
    await userEvent.click(screen.getByRole("button", { name: "Het is dezelfde brief" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Samenvoegen?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Samenvoegen" }));
    await waitFor(() =>
      expect(bodies).toEqual([{ keep_document_id: second.id, complete_twin_tasks: true }]),
    );
  });

  it("Taak ook afronden is shown only for a twin done on the other letter, on by default, and can be turned off", async () => {
    const bodies: unknown[] = [];
    const twin = {
      done_document_id: second.id,
      open_document_id: first.id,
      type: "pay",
      description: "Betaal de aanslag",
    };
    server.use(
      ...pairHandlers(pairOf({ task_twins: [twin] })),
      http.post(`${base}/duplicates/${pairId}/merge`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...documentDetail, ...first });
      }),
    );
    renderApp(path);
    await userEvent.click(await screen.findByRole("button", { name: "Het is dezelfde brief" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Samenvoegen?" });
    const box = within(dialog).getByRole("checkbox", { name: "Taak ook afronden" });
    expect(box).toBeChecked();
    expect(
      within(dialog).getByText("Op de andere scan is 'Betaal de aanslag' al afgerond."),
    ).toBeInTheDocument();
    await userEvent.click(box);
    await userEvent.click(within(dialog).getByRole("button", { name: "Samenvoegen" }));
    await waitFor(() =>
      expect(bodies).toEqual([{ keep_document_id: first.id, complete_twin_tasks: false }]),
    );
  });

  it("hides Taak ook afronden when the twin is open on the letter that goes", async () => {
    const twin = {
      done_document_id: first.id,
      open_document_id: second.id,
      type: "pay",
      description: "Betaal de aanslag",
    };
    server.use(...pairHandlers(pairOf({ task_twins: [twin] })));
    renderApp(path);
    await userEvent.click(await screen.findByRole("button", { name: "Het is dezelfde brief" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Samenvoegen?" });
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("Het zijn twee brieven dismisses and returns to Te doen", async () => {
    let dismissed = 0;
    server.use(
      ...pairHandlers(),
      http.post(`${base}/duplicates/${pairId}/dismiss`, () => {
        dismissed += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp(path);
    await userEvent.click(await screen.findByRole("button", { name: "Het zijn twee brieven" }));
    expect(
      await screen.findByText("Goed. We vragen het niet meer voor deze twee brieven."),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Te doen" })).toBeInTheDocument();
    expect(dismissed).toBe(1);
  });

  it("can_merge false disables Samenvoegen and shows the reason", async () => {
    server.use(...pairHandlers(pairOf({ can_merge: false, blocked_reason: "visibility_differs" })));
    renderApp(path);
    expect(await screen.findByRole("button", { name: "Het is dezelfde brief" })).toBeDisabled();
    expect(
      screen.getByText(
        "Deze twee brieven zijn niet voor dezelfde mensen zichtbaar. Daarom kun je ze niet samenvoegen. Kies 'Het zijn twee brieven', of laat ze eerst voor dezelfde mensen zichtbaar zijn.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Het zijn twee brieven" })).toBeEnabled();
  });

  it("404 shows Deze dubbele brieven zijn er niet (meer).", async () => {
    server.use(
      http.get(`${base}/duplicates/${pairId}`, () =>
        HttpResponse.json(
          errorBody("duplicate_not_found", "We kunnen deze dubbele brieven niet vinden."),
          { status: 404 },
        ),
      ),
      ...pairHandlers(),
    );
    renderApp(path);
    expect(
      await screen.findByText("Deze dubbele brieven zijn er niet (meer)."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Naar Te doen" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/inbox`,
    );
  });

  it("a viewer sees no choice", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      ...pairHandlers(),
    );
    renderApp(path);
    expect(await screen.findByText("Lijkt op elkaar voor 94%")).toBeInTheDocument();
    expect(await screen.findAllByRole("region", { name: /Brief [12]/ })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Het zijn twee brieven" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});
