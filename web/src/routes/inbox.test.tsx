import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { components } from "../api/schema";
import {
  API,
  belasting,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  papa,
  personHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

// The page is "Te doen"; its path stays `/inbox` so bookmarks keep working.
const path = `/w/${workspace.id}/inbox`;
const base = `*${API}/workspaces/${workspace.id}`;

type Summary = components["schemas"]["DocumentSummaryOut"];
type State = "new" | "review" | "unassigned";

function counts(value: Record<State, number> & { duplicate?: number }) {
  return http.get(`${base}/inbox/counts`, () => HttpResponse.json(value));
}

const reviewLetter: Summary = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b1111",
  sender: "Gemeente",
  status: "needs_review",
  inbox_state: "review",
  created_at: "2026-11-09T09:30:00Z",
};
const unassignedLetter: Summary = { ...documentSummary, created_at: "2026-11-10T09:30:00Z" };
const readingLetter: Summary = {
  ...documentSummary,
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b2222",
  sender: "Zorgverzekeraar",
  status: "processing",
  inbox_state: "new",
  // The newest letter: it still comes last, because nothing is needed for it yet.
  created_at: "2026-11-11T09:30:00Z",
};

/** Answers the list per `inbox_state`, the way the server filters it. */
function letters(list: Summary[], taken: () => string[] = () => []) {
  return http.get(`${base}/documents`, ({ request }) => {
    const state = new URL(request.url).searchParams.get("inbox_state");
    return HttpResponse.json(
      list.filter((document) => document.inbox_state === state && !taken().includes(document.id)),
    );
  });
}

function rowOf(sender: string): HTMLElement {
  return screen.getByRole("link", { name: sender }).closest("tr") as HTMLElement;
}

describe("te doen", () => {
  it("is named Te doen in the title, the heading and the navigation", async () => {
    server.use(counts({ new: 0, review: 0, unassigned: 0 }), ...loggedInHandlers(), letters([]));
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 1, name: "Te doen" })).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Te doen - Brievie"));
    expect(
      screen.getByText(
        "Brieven waar jij nog iets mee moet doen. Al je brieven, ook de afgehandelde, staan bij Alle post.",
      ),
    ).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Hoofdmenu" });
    expect(within(nav).getByRole("link", { name: "Te doen" })).toHaveAttribute("href", path);
  });

  it("shows one list without tabs, each letter with what it needs", async () => {
    server.use(
      counts({ new: 1, review: 1, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([reviewLetter, unassignedLetter, readingLetter]),
    );
    renderApp(path);
    await screen.findByRole("link", { name: "Zorgverzekeraar" });
    await screen.findByRole("link", { name: "Gemeente" });
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getAllByRole("table")).toHaveLength(1);

    expect(within(rowOf("Gemeente")).getByText("Controleren")).toBeInTheDocument();
    expect(
      within(rowOf("Gemeente")).getByText("Controleer deze brief in de app."),
    ).toBeInTheDocument();
    expect(within(rowOf("Belastingdienst")).getByText("Naam kiezen")).toBeInTheDocument();
    expect(
      within(rowOf("Belastingdienst")).getByRole("button", { name: /Op naam van/ }),
    ).toBeInTheDocument();
    expect(within(rowOf("Zorgverzekeraar")).getByText("Bezig…")).toBeInTheDocument();
    expect(
      within(rowOf("Zorgverzekeraar")).getByText(
        "Brievie leest deze brief nog. Je hoeft niets te doen.",
      ),
    ).toBeInTheDocument();
  });

  it("puts what needs you first, newest first, and letters being read last", async () => {
    server.use(
      counts({ new: 1, review: 1, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([reviewLetter, unassignedLetter, readingLetter]),
    );
    renderApp(path);
    await screen.findByRole("link", { name: "Zorgverzekeraar" });
    await screen.findByRole("link", { name: "Gemeente" });
    const senders = within(screen.getByRole("table"))
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent);
    expect(senders).toEqual(["Belastingdienst", "Gemeente", "Zorgverzekeraar"]);
  });

  it("keeps the numbers per kind of work in the header", async () => {
    server.use(
      counts({ new: 3, review: 1, unassigned: 2 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([]),
    );
    renderApp(path);
    expect(
      await screen.findByText("Controleren: 1 · Naam kiezen: 2 · Bezig: 3"),
    ).toBeInTheDocument();
  });

  it("says a letter that could not be read must be checked", async () => {
    server.use(
      counts({ new: 0, review: 1, unassigned: 0 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([{ ...reviewLetter, status: "failed" }]),
    );
    renderApp(path);
    await screen.findByRole("link", { name: "Gemeente" });
    expect(within(rowOf("Gemeente")).getByText("Controleren")).toBeInTheDocument();
    expect(
      within(rowOf("Gemeente")).getByText(
        "Brievie kon deze brief niet goed lezen. Controleer hem in de app.",
      ),
    ).toBeInTheDocument();
  });

  it("says you are done and where to go next when nothing waits", async () => {
    server.use(
      counts({ new: 0, review: 0, unassigned: 0 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([]),
    );
    renderApp(path);
    expect(await screen.findByText("Je bent klaar. Er is niets te doen.")).toBeInTheDocument();
    const inPanel = screen
      .getAllByRole("link", { name: "Alle post" })
      .find((link) => link.closest(".empty-state"));
    expect(inPanel).toHaveAttribute("href", `/w/${workspace.id}/post`);
  });

  it("ignores the tab of an old bookmark", async () => {
    server.use(
      counts({ new: 1, review: 1, unassigned: 0 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([reviewLetter, readingLetter]),
    );
    renderApp(`${path}?tab=new`);
    expect(await screen.findByRole("link", { name: "Gemeente" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Zorgverzekeraar" })).toBeInTheDocument();
  });

  it("puts a letter on a name and removes the row", async () => {
    const puts: unknown[] = [];
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 1 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, async ({ request }) => {
        puts.push(await request.json());
        taken.push(documentSummary.id);
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      letters([unassignedLetter], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    await user.click(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ }));
    await user.click(await screen.findByRole("option", { name: "Papa" }));
    await waitFor(() => expect(puts).toEqual([{ person_ids: [papa.id] }]));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument(),
    );
  });
});

describe("te doen labels and roles", () => {
  it("shows the labels of a letter as chips", async () => {
    server.use(
      counts({ new: 0, review: 0, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([{ ...unassignedLetter, label_ids: [belasting.id] }]),
    );
    renderApp(path);
    const row = (await screen.findByRole("link", { name: "Belastingdienst" })).closest("tr");
    expect(
      await within(row as HTMLElement).findByText("Belastingdienst", { selector: ".label-chip" }),
    ).toHaveClass("label-blue");
  });

  it("disables the person picker for a viewer", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      counts({ new: 0, review: 0, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([unassignedLetter]),
    );
    renderApp(path);
    expect(await screen.findByRole("button", { name: /Op naam van/ })).toBeDisabled();
  });
});

describe("te doen focus", () => {
  const second = {
    ...unassignedLetter,
    id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b5555",
    sender: "Gemeente",
  };
  const third = {
    ...unassignedLetter,
    id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b6666",
    sender: "Zorgverzekeraar",
  };

  async function assign(user: ReturnType<typeof userEvent.setup>, sender: string) {
    const row = (await screen.findByRole("link", { name: sender })).closest("tr");
    await user.click(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ }));
    await user.click(await screen.findByRole("option", { name: "Papa" }));
  }

  it("moves focus to the picker of the row that takes its place", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 3 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () => {
        taken.push(documentSummary.id);
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      letters([unassignedLetter, second, third], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await screen.findByRole("link", { name: "Zorgverzekeraar" });
    await assign(user, "Belastingdienst");
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument(),
    );
    const next = (await screen.findByRole("link", { name: "Gemeente" })).closest("tr");
    await waitFor(() =>
      expect(
        within(next as HTMLElement).getByRole("button", { name: /Op naam van/ }),
      ).toHaveFocus(),
    );
  });

  it("moves focus to the previous row when the last row left", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 2 }),
      http.put(`${base}/documents/${second.id}/addressees`, () => {
        taken.push(second.id);
        return HttpResponse.json({ ...second, addressee_ids: [papa.id] });
      }),
      letters([unassignedLetter, second], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await screen.findByRole("link", { name: "Belastingdienst" });
    await assign(user, "Gemeente");
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Gemeente" })).not.toBeInTheDocument(),
    );
    const previous = screen.getByRole("link", { name: "Belastingdienst" }).closest("tr");
    await waitFor(() =>
      expect(
        within(previous as HTMLElement).getByRole("button", { name: /Op naam van/ }),
      ).toHaveFocus(),
    );
  });

  it("moves focus to the sender of a row without a picker", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 1, unassigned: 1 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () => {
        taken.push(documentSummary.id);
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      letters([unassignedLetter, reviewLetter], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await screen.findByRole("link", { name: "Gemeente" });
    await assign(user, "Belastingdienst");
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Belastingdienst" })).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByRole("link", { name: "Gemeente" })).toHaveFocus());
  });

  it("moves focus to the empty panel when the list is empty", async () => {
    const taken: string[] = [];
    server.use(
      counts({ new: 0, review: 0, unassigned: 1 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () => {
        taken.push(documentSummary.id);
        return HttpResponse.json({ ...documentSummary, addressee_ids: [papa.id] });
      }),
      letters([unassignedLetter], () => taken),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await assign(user, "Belastingdienst");
    const note = await screen.findByText("Je bent klaar. Er is niets te doen.");
    await waitFor(() => expect(note.closest("section")).toHaveFocus());
  });

  it("keeps focus on the picker and shows the error when the PUT fails", async () => {
    server.use(
      counts({ new: 0, review: 0, unassigned: 2 }),
      http.put(`${base}/documents/${documentSummary.id}/addressees`, () =>
        HttpResponse.json({ code: "forbidden", message: "Dat mag je niet." }, { status: 403 }),
      ),
      letters([unassignedLetter, second]),
      ...loggedInHandlers(),
      ...personHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await assign(user, "Belastingdienst");
    // A multi-select stays open after a choice: closing it hands focus back to the picker.
    await screen.findByText("Dat mag je niet.");
    await user.keyboard("{Escape}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Dat mag je niet.");
    const row = screen.getByRole("link", { name: "Belastingdienst" }).closest("tr");
    await waitFor(() =>
      expect(within(row as HTMLElement).getByRole("button", { name: /Op naam van/ })).toHaveFocus(),
    );
  });
  it("Te doen shows Dubbel? with its count and hides it at 0", async () => {
    const pair: components["schemas"]["DuplicateOut"] = {
      id: "c1111111-1111-4111-8111-111111111111",
      state: "open",
      created_at: "2026-11-11T09:31:00Z",
      first: { ...documentSummary, sender: "Waterschap" },
      second: { ...reviewLetter, sender: "Waterschap", letter_date: "2026-11-06" },
      suggested_keep_id: documentSummary.id,
      text_similarity: 91,
      can_merge: true,
      blocked_reason: "",
    };
    server.use(
      counts({ new: 0, review: 0, unassigned: 0, duplicate: 1 }),
      http.get(`${base}/duplicates`, () => HttpResponse.json([pair])),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([]),
    );
    renderApp(path);
    const section = await screen.findByRole("region", { name: "Dubbel? 1" });
    const link = await within(section).findByRole("link", { name: /Lijkt op elkaar voor 91%/ });
    expect(link).toHaveAttribute("href", `/w/${workspace.id}/dubbel/${pair.id}`);
    expect(within(section).getAllByText("Waterschap")).toHaveLength(2);
    expect(screen.getByText("Dubbel?: 1")).toBeInTheDocument();
  });

  it("hides Dubbel? at 0", async () => {
    server.use(
      counts({ new: 0, review: 1, unassigned: 0, duplicate: 0 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([reviewLetter]),
    );
    renderApp(path);
    await screen.findByRole("link", { name: "Gemeente" });
    expect(await screen.findByText("Controleren: 1")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /Dubbel\?/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Dubbel?")).not.toBeInTheDocument();
  });

  it("a letter with an open pair carries the chip Dubbel?", async () => {
    server.use(
      counts({ new: 0, review: 1, unassigned: 1 }),
      ...loggedInHandlers(),
      ...personHandlers(),
      letters([{ ...reviewLetter, open_duplicate_count: 1 }, unassignedLetter]),
    );
    renderApp(path);
    await screen.findByRole("link", { name: "Gemeente" });
    expect(within(rowOf("Gemeente")).getByText("Dubbel?")).toBeInTheDocument();
    expect(within(rowOf("Belastingdienst")).queryByText("Dubbel?")).not.toBeInTheDocument();
  });
});
