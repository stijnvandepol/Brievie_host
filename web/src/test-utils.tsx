import { I18nProvider } from "@lingui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { components } from "./api/schema";
import { i18n } from "./i18n";
import { LocaleRouter } from "./locale-router";
import { createAppRouter } from "./router";
import { trackApp } from "./test-apps";
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
    http.get(`*${API}/me/notices`, () => HttpResponse.json([])),
    http.get(`*${API}/workspaces/${workspace.id}/inbox/counts`, () =>
      HttpResponse.json({ new: 0, review: 0, unassigned: 0 }),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/my-tasks/counts`, () =>
      HttpResponse.json(noTaskCounts),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/visibility`, () =>
      HttpResponse.json(homeVisibility),
    ),
  ];
}

/** "Wie ziet wat" of a Thuis workspace: everyone sees all post, no departments. */
export const homeVisibility: components["schemas"]["VisibilityOut"] = {
  kind: "home",
  everyone_sees_all: true,
  departments_enabled: false,
  admins_read_all: true,
  sorter_user_ids: [],
  sentences: [],
  summary: [],
};

export const noTaskCounts = { mine: 0, overdue: 0, within_week: 0, needs_date: 0, to_pick_up: 0 };

/**
 * Two workspaces: `/` shows the list instead of going straight to the inbox. With one
 * workspace the "Werkruimtes" page is only a moment on the way to the inbox, so a test
 * that waits for it races the redirect.
 */
export function twoWorkspaces() {
  return http.get(`*${API}/workspaces`, () => HttpResponse.json([workspace, secondWorkspace]));
}

/**
 * Holds a handler until the test calls `open()`, so a test can look at a pending state
 * for as long as it needs. A fixed delay in a handler races the test on a busy machine.
 */
export function gate() {
  let release = () => {};
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { opened, open: () => release() };
}

export function errorBody(code: string, message: string) {
  return { code, message };
}

export function renderApp(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createAppRouter(queryClient, createMemoryHistory({ initialEntries: [path] }));
  trackApp({ router, queryClient });
  render(
    <I18nProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <LocaleRouter router={router} />
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
  confirmed_by_ai: false,
  auto_label_ids: [],
  auto_addressee_ids: [],
  proposed_sender: "",
  proposed_document_type: "",
  proposed_letter_date: null,
  open_duplicate_count: 0,
  visibility: "workspace",
  reader_names: [],
};

export const documentDetail: components["schemas"]["DocumentOut"] = {
  ...documentSummary,
  can_make_public: false,
  shared_user_ids: [],
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
      completed_at: null,
      created_by_ai: false,
      amount: "",
      term_quote: "",
      assigned_to_name: "",
      assigned_to_id: null,
    },
  ],
  suggestions: [],
  can_read: true,
};

type Person = components["schemas"]["PersonOut"];

export const papa: Person = {
  id: "a1111111-1111-4111-8111-111111111111",
  display_name: "Papa",
  name_variants: ["P. de Vries"],
  archived: false,
  document_count: 2,
  user_id: null,
  kind: "person",
};

export const annaPerson: Person = {
  id: "a2222222-2222-4222-8222-222222222222",
  display_name: "Anna",
  name_variants: [],
  archived: false,
  document_count: 0,
  user_id: user.id,
  kind: "person",
};

export const members: components["schemas"]["MemberOut"][] = [
  {
    user_id: user.id,
    name: "Anna",
    role: "admin",
    person_id: annaPerson.id,
    can_manage_account: false,
    sorts_post: false,
  },
  {
    user_id: "b3333333-3333-4333-8333-333333333333",
    name: "Bram",
    role: "member",
    person_id: null,
    can_manage_account: true,
    sorts_post: false,
  },
];

type Label = components["schemas"]["LabelOut"];

export const belasting: Label = {
  id: "d1111111-1111-4111-8111-111111111111",
  name: "Belastingdienst",
  color: "blue",
  system_key: "belastingdienst",
  document_count: 2,
};

export const sales: Label = {
  id: "d2222222-2222-4222-8222-222222222222",
  name: "Sales",
  color: "teal",
  system_key: "",
  document_count: 0,
};

export function labelHandlers(labels: Label[] = [belasting, sales]) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/labels`, () => HttpResponse.json(labels)),
    ...aiHandlers(),
  ];
}

/** The AI mode of the first workspace (default: off), with what the server says besides. */
export function aiHandlers(
  mode = "off",
  rest: Partial<Omit<components["schemas"]["AiStatusOut"], "mode">> = {},
) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/ai`, () => HttpResponse.json({ mode, ...rest })),
  ];
}

/** The persons, members and labels of the first workspace. */
export function personHandlers(persons: Person[] = [papa, annaPerson]) {
  return [
    ...labelHandlers(),
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

type Trashed = components["schemas"]["TrashedDocumentOut"];

/** A letter in the trash: the summary row plus when, by whom and until when. */
export function trashedLetter(overrides: Partial<Trashed> = {}): Trashed {
  return {
    ...documentSummary,
    trashed_at: "2026-11-09T10:00:00Z",
    trashed_by_name: "Anna",
    purge_after: "2026-12-09T10:00:00Z",
    ...overrides,
  };
}

/** The trash of the first workspace; the list is read again from `items` on every call. */
/** `total`: how many letters the trash holds (the listed ones when absent). */
export function trashHandlers(items: Trashed[] = [], total?: number) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/trash`, () =>
      HttpResponse.json({ items, retention_days: 30, total: total ?? items.length }),
    ),
  ];
}

type Rule = components["schemas"]["RuleOut"];

export const apkRule: Rule = {
  id: "f1111111-1111-4111-8111-111111111111",
  position: 0,
  name: "APK naar Auto",
  active: true,
  mode: "propose",
  stop: false,
  match: "all",
  conditions: [{ field: "text", op: "contains_word", value: "APK" }],
  actions: [{ type: "add_label", label_id: sales.id }],
  system_key: "",
  applied_count: 12,
  proposed_count: 3,
};

export const bankRule: Rule = {
  ...apkRule,
  id: "f2222222-2222-4222-8222-222222222222",
  position: 1,
  name: "Bank",
  active: false,
  mode: "auto",
  conditions: [{ field: "sender", op: "contains", value: "ING" }],
  actions: [{ type: "add_addressee", person_id: papa.id }],
  applied_count: 0,
  proposed_count: 0,
};

type RuleTemplate = components["schemas"]["RuleTemplateOut"];

/** Three of the server's example rules, as `listRuleTemplates` returns them. */
export const senderTemplate: RuleTemplate = {
  key: "label_by_sender",
  kinds: ["home", "business"],
  title: "Label op afzender",
  description: "Stelt een label voor op alle post van één afzender.",
  tip: "Preciezer dan een woord in de brief.",
  name: "Label op afzender",
  mode: "propose",
  match: "all",
  conditions: [
    {
      field: "sender",
      op: "contains_word",
      value: "",
      example: "Zilveren Kruis",
      hint: "Typ de naam van de afzender, zoals die op de brief staat.",
    },
  ],
  actions: [
    {
      type: "add_label",
      hint: "Kies het label dat de brief krijgt.",
      label_key: "",
      label_name: "",
    },
  ],
};

export const supplierTemplate: RuleTemplate = {
  ...senderTemplate,
  key: "supplier_invoice",
  kinds: ["business"],
  title: "Facturen van een leverancier",
  description: "Stelt een label voor op facturen van één leverancier.",
  tip: "",
  name: "Facturen van een leverancier",
};

export const wozTemplate: RuleTemplate = {
  key: "municipality_woz",
  kinds: ["home"],
  title: "De WOZ-beschikking van de gemeente",
  description: "Stelt het label Wonen voor op post van de gemeente waarin het woord WOZ staat.",
  tip: "Twee voorwaarden samen zijn preciezer dan één.",
  name: "WOZ van de gemeente",
  mode: "propose",
  match: "all",
  conditions: [
    { field: "sender", op: "contains_word", value: "gemeente", example: "", hint: "" },
    { field: "text", op: "contains_word", value: "WOZ", example: "", hint: "" },
  ],
  actions: [
    {
      type: "add_label",
      hint: "Kies het label dat de brief krijgt.",
      label_key: "wonen",
      label_name: "Wonen",
    },
  ],
};

/** The rules of the first workspace, plus the labels and persons their sentences need. */
export function ruleHandlers(
  rules: Rule[] = [apkRule, bankRule],
  templates: RuleTemplate[] = [supplierTemplate, senderTemplate, wozTemplate],
) {
  return [
    ...personHandlers(),
    http.get(`*${API}/workspaces/${workspace.id}/rules`, () => HttpResponse.json(rules)),
    http.get(`*${API}/workspaces/${workspace.id}/rules/templates`, () =>
      HttpResponse.json(templates),
    ),
  ];
}

/** The caller's export in its states: `none`, `pending`, `ready`, `failed`. */
export function exportState(
  status: "none" | "pending" | "ready" | "failed" = "none",
  allowed = true,
): components["schemas"]["MyExportOut"] {
  return {
    allowed,
    status,
    document_count: 0,
    size: 0,
    ...(status === "ready"
      ? {
          id: "ee111111-1111-4111-8111-111111111111",
          created_at: "2026-11-18T09:00:00Z",
          expires_at: "2099-11-19T09:00:00Z",
          document_count: 3,
          size: 1234,
        }
      : {}),
  };
}

/** What the page "Leden" asks: members, open invitations and the caller's export. */
export function memberHandlers(
  list: components["schemas"]["MemberOut"][] = members,
  invitations: components["schemas"]["InvitationOut"][] = [],
  myExport: components["schemas"]["MyExportOut"] = exportState(),
) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/members`, () => HttpResponse.json(list)),
    http.get(`*${API}/workspaces/${workspace.id}/invitations`, () =>
      HttpResponse.json(invitations),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/my-export`, () => HttpResponse.json(myExport)),
  ];
}

/** The first workspace with the caller in another role. */
export function asRole(role: "admin" | "member" | "viewer", kind: "home" | "business" = "home") {
  return http.get(`*${API}/workspaces`, () => HttpResponse.json([{ ...workspace, role, kind }]));
}

/**
 * Remembers whether an element with role "alert" was ever in the page: a flash of an error
 * that is gone again when the test looks. Call `stop()` at the end.
 */
export function watchAlerts() {
  let seen = document.querySelector('[role="alert"]') !== null;
  const observer = new MutationObserver(() => {
    if (document.querySelector('[role="alert"]') !== null) seen = true;
  });
  observer.observe(document.body, { childList: true, subtree: true });
  return {
    seen: () => seen,
    stop: () => observer.disconnect(),
  };
}
