import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { loggedInHandlers, renderApp, server } from "../test-utils";

describe("workspace", () => {
  it("shows not found for a workspace id that is not in the list", async () => {
    server.use(...loggedInHandlers());
    renderApp("/w/00000000-0000-4000-8000-000000000000/post");
    expect(
      await screen.findByRole("heading", { level: 1, name: /deze pagina bestaat niet/i }),
    ).toBeInTheDocument();
  });
});
