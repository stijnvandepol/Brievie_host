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

async function fill(
  u: ReturnType<typeof userEvent.setup>,
  current: string,
  next: string,
  again: string,
) {
  await u.type(await screen.findByLabelText("Huidig wachtwoord"), current);
  await u.type(screen.getByLabelText("Nieuw wachtwoord"), next);
  await u.type(screen.getByLabelText("Herhaal nieuw wachtwoord"), again);
}

describe("change my password", () => {
  it("reports that the other sessions ended", async () => {
    let body: unknown = null;
    server.use(
      ...loggedInHandlers(),
      http.post(`*${API}/me/password`, async ({ request }) => {
        body = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const u = userEvent.setup();
    renderApp("/account/wachtwoord");
    await fill(u, "oud wachtwoord 1", "nieuw wachtwoord 22", "nieuw wachtwoord 22");
    await u.click(screen.getByRole("button", { name: "Wachtwoord wijzigen" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Je wachtwoord is gewijzigd. Je andere sessies zijn beëindigd.",
    );
    expect(body).toEqual({
      current_password: "oud wachtwoord 1",
      new_password: "nieuw wachtwoord 22",
    });
    expect(screen.getByLabelText("Huidig wachtwoord")).toHaveValue("");
  });

  it("does not ask the server when the two new passwords differ", async () => {
    server.use(...loggedInHandlers());
    const u = userEvent.setup();
    renderApp("/account/wachtwoord");
    await fill(u, "oud wachtwoord 1", "nieuw wachtwoord 22", "anders wachtwoord 33");
    await u.click(screen.getByRole("button", { name: "Wachtwoord wijzigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "De twee nieuwe wachtwoorden zijn niet hetzelfde.",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows the server's sentence for a wrong current password", async () => {
    server.use(
      ...loggedInHandlers(),
      http.post(`*${API}/me/password`, () =>
        HttpResponse.json(errorBody("wrong_password", "Je huidige wachtwoord klopt niet."), {
          status: 400,
        }),
      ),
    );
    const u = userEvent.setup();
    renderApp("/account/wachtwoord");
    await fill(u, "fout", "nieuw wachtwoord 22", "nieuw wachtwoord 22");
    await u.click(screen.getByRole("button", { name: "Wachtwoord wijzigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Je huidige wachtwoord klopt niet.");
  });
});
