import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { api } from "./api/client";
import { dateFormat } from "./format";
import { currentLocale, i18n, setLocale } from "./i18n";
import {
  API,
  errorBody,
  HttpResponse,
  http,
  loggedInHandlers,
  renderApp,
  server,
  user,
} from "./test-utils";

function loggedOut(languages: string[] = []) {
  server.use(
    http.get(`*${API}/setup`, ({ request }) => {
      languages.push(request.headers.get("Accept-Language") ?? "");
      return HttpResponse.json({ needs_setup: false });
    }),
    http.get(`*${API}/me`, () =>
      HttpResponse.json(errorBody("not_authenticated", "Je bent niet ingelogd."), { status: 401 }),
    ),
  );
}

describe("language", () => {
  it("switches the sign-in page to English, with <html lang> and the server's language", async () => {
    const languages: string[] = [];
    loggedOut(languages);
    renderApp("/login");
    await screen.findByRole("heading", { level: 1, name: "Inloggen" });
    expect(document.documentElement.lang).toBe("nl");
    expect(languages.at(-1)).toBe("nl");

    await userEvent.click(screen.getByRole("button", { name: "English" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.lang).toBe("en");
    expect(localStorage.getItem("brievie.language")).toBe("en");
    await api.GET("/api/v1/setup");
    expect(languages.at(-1)).toBe("en");
  });

  it("follows the account's own choice once signed in", async () => {
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json({ ...user, language: "en" })),
      ...loggedInHandlers(),
    );
    renderApp("/account/taal");

    expect(await screen.findByRole("heading", { level: 1, name: "Language" })).toBeInTheDocument();
    expect(currentLocale()).toBe("en");
    expect(screen.getByRole("radio", { name: "English" })).toBeChecked();
  });

  it("keeps the browser's language while the account has no choice", async () => {
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json({ ...user, language: "" })),
      ...loggedInHandlers(),
    );
    renderApp("/account/taal");

    expect(await screen.findByRole("heading", { level: 1, name: "Taal" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Zoals je browser of telefoon" })).toBeChecked();
  });

  it("stores the choice on the account and switches the interface", async () => {
    const sent: unknown[] = [];
    server.use(
      http.get(`*${API}/me`, () => HttpResponse.json({ ...user, language: "" })),
      ...loggedInHandlers(),
      http.put(`*${API}/me/language`, async ({ request }) => {
        const body = (await request.json()) as { language: string };
        sent.push(body);
        return HttpResponse.json({ ...user, language: body.language });
      }),
    );
    renderApp("/account/taal");
    await screen.findByRole("heading", { level: 1, name: "Taal" });

    await userEvent.click(screen.getByRole("radio", { name: "English" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Language" })).toBeInTheDocument();
    expect(sent).toEqual([{ language: "en" }]);
    expect(i18n.locale).toBe("en");
  });

  it("formats dates in the active language", () => {
    const day = dateFormat({ dateStyle: "long", timeZone: "UTC" });
    const date = new Date(Date.UTC(2026, 10, 21));
    expect(day.format(date)).toBe("21 november 2026");
    setLocale("en");
    expect(day.format(date)).toMatch(/November/);
  });
});
