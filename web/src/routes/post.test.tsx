import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  API,
  documentHandlers,
  documentSummary,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  workspace,
} from "../test-utils";

describe("post", () => {
  it("lists documents with a link per sender", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post`);
    const link = await screen.findByRole("link", { name: "Belastingdienst" });
    expect(link).toHaveAttribute("href", expect.stringContaining("/post/"));
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Aanslag")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Datum brief" })).toBeInTheDocument();
  });

  it("names an unknown sender", async () => {
    server.use(
      ...loggedInHandlers(),
      http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
        HttpResponse.json([{ ...documentSummary, sender: "" }]),
      ),
    );
    renderApp(`/w/${workspace.id}/post`);
    expect(await screen.findByRole("link", { name: "Onbekende afzender" })).toBeInTheDocument();
  });

  it("names the workspace in the sidebar and links Post", async () => {
    server.use(...loggedInHandlers(), ...documentHandlers());
    renderApp(`/w/${workspace.id}/post`);
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByText("Thuis")).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Post" })).toHaveAttribute(
      "href",
      `/w/${workspace.id}/post`,
    );
    expect(within(nav).getByRole("link", { name: "Werkruimtes" })).toBeInTheDocument();
  });
});
