import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  documentDetail,
  documentHandlers,
  documentSummary,
  loggedInHandlers,
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
});
