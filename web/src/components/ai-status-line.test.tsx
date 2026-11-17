import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  API,
  aiHandlers,
  HttpResponse,
  http,
  loggedInHandlers,
  personHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

const base = `*${API}/workspaces/${workspace.id}`;

function post() {
  return [
    ...loggedInHandlers(),
    ...personHandlers(),
    http.get(`${base}/documents`, () => HttpResponse.json([])),
  ];
}

describe("AI status line", () => {
  it("says where the text of letters goes and since when", async () => {
    server.use(
      ...aiHandlers("external", {
        supplier: "mistral",
        host: "api.mistral.ai",
        since: "2026-11-17T14:39:59.238Z",
      }),
      ...post(),
    );
    renderApp(`/w/${workspace.id}/post`);
    expect(
      await screen.findByText(
        "AI aan: tekst van brieven gaat naar Mistral in Frankrijk (api.mistral.ai), ingesteld op 17 november 2026.",
      ),
    ).toBeInTheDocument();
  });

  it("names only the address of a service it does not know", async () => {
    server.use(
      ...aiHandlers("external", {
        supplier: "custom",
        host: "llm.example.com",
        since: "2026-11-17T14:39:59.238Z",
      }),
      ...post(),
    );
    renderApp(`/w/${workspace.id}/post`);
    expect(
      await screen.findByText(
        "AI aan: tekst van brieven gaat naar llm.example.com, ingesteld op 17 november 2026.",
      ),
    ).toBeInTheDocument();
  });

  it("says that no letter is read when nobody agreed", async () => {
    server.use(...aiHandlers("external", { host: "api.mistral.ai" }), ...post());
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByText(/niemand heeft daar akkoord voor gegeven/)).toBeInTheDocument();
  });

  it("says that nothing leaves the own network", async () => {
    server.use(...aiHandlers("local", { since: "2026-11-17T14:39:59.238Z" }), ...post());
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByText(/AI aan op je eigen netwerk/)).toBeInTheDocument();
  });

  it("says that the AI is off, also for a server that only knows the mode", async () => {
    server.use(...aiHandlers("off"), ...post());
    renderApp(`/w/${workspace.id}/post`);
    expect(
      await screen.findByText("AI uit: de tekst van brieven gaat naar geen enkele AI-dienst."),
    ).toBeInTheDocument();
  });
});
