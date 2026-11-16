import { I18nProvider } from "@lingui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { components } from "./api/schema";
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
    http.get(`*${API}/devices`, () => HttpResponse.json([])),
    http.get(`*${API}/workspaces/${workspace.id}/inbox/counts`, () =>
      HttpResponse.json({ new: 0, review: 0, unassigned: 0 }),
    ),
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

export const secondWorkspace = {
  id: "5d1e7c3a-2b4f-4a6e-9c8d-1f2a3b4c5555",
  name: "Bedrijf",
  kind: "business",
  role: "member",
};

export const documentSummary = {
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4444",
  client_id: "3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0999",
  created_at: "2026-11-10T09:30:00Z",
  document_type: "assessment",
  letter_date: "2026-11-05",
  page_count: 3,
  sender: "Belastingdienst",
  status: "done",
  addressee_ids: [],
  label_ids: [],
  inbox_state: "unassigned",
  open_suggestion_count: 0,
};

export const documentDetail: components["schemas"]["DocumentOut"] = {
  ...documentSummary,
  failure_code: "",
  pages: [
    { index: 1, ocr_text: "Eerste pagina" },
    { index: 2, ocr_text: "Tweede pagina" },
    { index: 3, ocr_text: "Derde pagina" },
  ],
  proposals: [],
  summary: "Je krijgt een aanslag van 120 euro.",
  summary_by_ai: true,
  tasks: [
    {
      id: "0f0e0d0c-0b0a-4908-8706-050403020101",
      type: "pay",
      description: "Betaal de aanslag",
      deadline: "2026-12-01",
      deadline_quote: "uiterlijk 1 december",
      deadline_source: "letter",
      status: "open",
    },
  ],
};

type Person = components["schemas"]["PersonOut"];

export const papa: Person = {
  id: "a1111111-1111-4111-8111-111111111111",
  display_name: "Papa",
  name_variants: ["P. de Vries"],
  archived: false,
  document_count: 2,
  user_id: null,
};

export const annaPerson: Person = {
  id: "a2222222-2222-4222-8222-222222222222",
  display_name: "Anna",
  name_variants: [],
  archived: false,
  document_count: 0,
  user_id: user.id,
};

export const members: components["schemas"]["MemberOut"][] = [
  { user_id: user.id, name: "Anna", role: "admin", person_id: annaPerson.id },
  {
    user_id: "b3333333-3333-4333-8333-333333333333",
    name: "Bram",
    role: "member",
    person_id: null,
  },
];

export function personHandlers(persons: Person[] = [papa, annaPerson]) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/persons`, () => HttpResponse.json(persons)),
    http.get(`*${API}/workspaces/${workspace.id}/members`, () => HttpResponse.json(members)),
  ];
}

/** The post endpoints of the first workspace: the list and one document with its pages. */
export function documentHandlers(detail: components["schemas"]["DocumentOut"] = documentDetail) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/documents`, () =>
      HttpResponse.json([documentSummary]),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}`, () =>
      HttpResponse.json(detail),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`, () =>
      HttpResponse.json([]),
    ),
    ...personHandlers(),
  ];
}
