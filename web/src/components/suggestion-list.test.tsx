import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  belasting,
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
const suggestionsUrl = `*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/suggestions`;

const byName = {
  id: "5b5b5b5b-1111-4a4a-8b8b-000000000001",
  action: "add_addressee",
  person_id: papa.id,
  label_id: null,
  origin: "name_match",
  rule_name: "",
  quote: "T.a.v. P. de Vries",
  confidence: "0.950",
  preselected: true,
};
const byAi = {
  id: "5b5b5b5b-1111-4a4a-8b8b-000000000002",
  action: "add_label",
  person_id: null,
  label_id: belasting.id,
  origin: "ai",
  rule_name: "",
  quote: "",
  confidence: "0.800",
  preselected: false,
};
const byRule = { ...byAi, id: "5b5b5b5b-1111-4a4a-8b8b-000000000003", origin: "rule" };
const withSuggestions = {
  ...documentDetail,
  suggestions: [byName, byAi],
  open_suggestion_count: 2,
};

describe("suggestion list", () => {
  it("shows each suggestion as a question with its origin and quote", async () => {
    server.use(
      ...loggedInHandlers(),
      ...documentHandlers({
        ...withSuggestions,
        suggestions: [byName, byAi, { ...byRule, rule_name: "Post van de fiscus" }],
      }),
    );
    renderApp(path);
    const list = await screen.findByRole("region", { name: "Voorstellen" });
    expect(await within(list).findByText("Op naam van Papa?")).toBeInTheDocument();
    expect(within(list).getAllByText("Label Belastingdienst?")).toHaveLength(2);
    expect(within(list).getByText("Herkend in de brief")).toBeInTheDocument();
    expect(within(list).getByText("T.a.v. P. de Vries")).toBeInTheDocument();
    expect(within(list).getByText("Voorstel van AI")).toBeInTheDocument();
    expect(within(list).getByText("Regel: Post van de fiscus")).toBeInTheDocument();
  });

  it("accepts a suggestion and it leaves the list", async () => {
    const posts: string[] = [];
    let current = withSuggestions;
    server.use(
      http.post(`${suggestionsUrl}/:id/accept`, ({ params }) => {
        posts.push(String(params.id));
        current = {
          ...withSuggestions,
          addressee_ids: [papa.id],
          suggestions: [byAi],
          open_suggestion_count: 1,
        };
        return HttpResponse.json(current);
      }),
      http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, () =>
        HttpResponse.json(current),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(await screen.findByRole("button", { name: "Toevoegen: Op naam van Papa?" }));
    await waitFor(() => expect(posts).toEqual([byName.id]));
    await waitFor(() => expect(screen.queryByText("Op naam van Papa?")).not.toBeInTheDocument());
    expect(screen.getByText("Label Belastingdienst?")).toBeInTheDocument();
  });

  it("dismisses a suggestion", async () => {
    const posts: string[] = [];
    server.use(
      http.post(`${suggestionsUrl}/:id/dismiss`, ({ params }) => {
        posts.push(String(params.id));
        return HttpResponse.json({ ...withSuggestions, suggestions: [byName] });
      }),
      ...loggedInHandlers(),
      ...documentHandlers(withSuggestions),
    );
    const user = userEvent.setup();
    renderApp(path);
    await user.click(
      await screen.findByRole("button", { name: "Negeren: Label Belastingdienst?" }),
    );
    await waitFor(() => expect(posts).toEqual([byAi.id]));
  });

  it("shows a viewer the suggestions without buttons", async () => {
    server.use(
      http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role: "viewer" }])),
      ...loggedInHandlers(),
      ...documentHandlers(withSuggestions),
    );
    renderApp(path);
    expect(await screen.findByText("Op naam van Papa?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Toevoegen/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Negeren/ })).not.toBeInTheDocument();
  });

  it("marks a letter with open suggestions in the post list", async () => {
    server.use(
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, open_suggestion_count: 2 }]),
      ),
      ...loggedInHandlers(),
      ...documentHandlers(),
    );
    renderApp(`/w/${workspace.id}/post`);
    const row = (await screen.findByRole("rowheader", { name: "Belastingdienst" })).closest("tr");
    expect(within(row as HTMLElement).getByText("voorstel")).toBeInTheDocument();
  });
});
