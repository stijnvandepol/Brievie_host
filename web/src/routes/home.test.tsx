import { screen } from "@testing-library/react";
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

describe("home", () => {
  it("lists the workspaces with their kind and links to pairing the app from the menu", async () => {
    server.use(...loggedInHandlers());
    renderApp("/");
    expect(await screen.findByRole("heading", { name: /werkruimtes/i })).toBeInTheDocument();
    expect(await screen.findByText("Thuis", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /app koppelen/i })).toHaveAttribute(
      "href",
      "/koppel-app",
    );
  });

  it("logs out with the CSRF token and returns to the login screen", async () => {
    server.use(...loggedInHandlers());
    document.cookie = "csrftoken=tok123; path=/";
    let csrf: string | null = null;
    server.use(
      http.post(`*${API}/auth/logout`, ({ request }) => {
        csrf = request.headers.get("x-csrftoken");
        server.use(
          http.get(`*${API}/me`, () =>
            HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), {
              status: 401,
            }),
          ),
        );
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    const { router } = renderApp("/");
    await u.click(await screen.findByRole("button", { name: /uitloggen/i }));
    expect(await screen.findByRole("button", { name: /^inloggen$/i })).toBeInTheDocument();
    expect(csrf).toBe("tok123");
    expect(router.state.location.pathname).toBe("/login");
  });
});
