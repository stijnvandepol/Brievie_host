import { I18nProvider } from "@lingui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { i18n } from "./i18n";
import { createAppRouter } from "./router";
import { server } from "./test-server";

export const API = "/api/v1";

export const user = {
  id: "7c1d6a58-6a53-4b8b-9d2f-0a3f5d0a1111",
  email: "anna@example.nl",
  name: "Anna",
  language: "nl",
  explanation_language: "nl",
};

export const workspace = {
  id: "0b6f9a2e-8f5c-4f43-8a39-5a6b6d0c2222",
  name: "Thuis",
  kind: "home",
  role: "admin",
};

/** Answers the guard calls of a server that is set up and a visitor who is logged in. */
export function loggedInHandlers() {
  return [
    http.get(`*${API}/setup`, () => HttpResponse.json({ needs_setup: false })),
    http.get(`*${API}/me`, () => HttpResponse.json(user)),
    http.get(`*${API}/workspaces`, () => HttpResponse.json([workspace])),
  ];
}

export function errorBody(code: string, message: string) {
  return { code, message };
}

export function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createAppRouter(queryClient, createMemoryHistory({ initialEntries: [path] }));
  render(
    <I18nProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nProvider>,
  );
  return { router, queryClient };
}

export { HttpResponse, http, server };
