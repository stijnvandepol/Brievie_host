import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  API,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
} from "../test-utils";

describe("router screens", () => {
  it("shows a Dutch not-found page with a link to the start page", async () => {
    server.use(...loggedInHandlers());
    renderApp("/bestaat-niet");

    expect(await screen.findByText("Deze pagina bestaat niet.")).toBeInTheDocument();
    expect(screen.queryByText(/not found/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /startpagina/i })).toHaveAttribute("href", "/");
  });

  it("shows the server's message when a page cannot load, and tries again on request", async () => {
    server.use(
      http.get(`*${API}/setup`, () =>
        HttpResponse.json(errorBody("internal_error", "Er ging iets mis op de server."), {
          status: 500,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/");

    expect(await screen.findByRole("alert")).toHaveTextContent("Er ging iets mis op de server.");
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();

    server.use(...loggedInHandlers());
    await u.click(screen.getByRole("button", { name: "Probeer opnieuw" }));
    expect(await screen.findByRole("heading", { name: /werkruimtes/i })).toBeInTheDocument();
  });

  it("moves focus to the heading of the new page after a route change", async () => {
    server.use(...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/");
    await screen.findByRole("heading", { name: /werkruimtes/i });

    await u.click(screen.getByRole("link", { name: /app koppelen/i }));

    const heading = await screen.findByRole("heading", { level: 1, name: /app koppelen/i });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(heading).toHaveAttribute("tabindex", "-1");
  });
});
