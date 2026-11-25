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
    http.get(`*${API}/workspaces/${workspace.id}/menu/counts`, () =>
      HttpResponse.json(noMenuCounts),
    ),
  ];
}

export const noMenuCounts: components["schemas"]["MenuCountsOut"] = {
  inbox: 0,
  my_post_unread: 0,
  my_tasks: 0,
  my_tasks_overdue: 0,
  lingering: 0,
  shows_lingering: false,
  has_person: true,
};

/** The menu's numbers of the first workspace. */
export function menuCounts(counts: Partial<components["schemas"]["MenuCountsOut"]>) {
  return http.get(`*${API}/workspaces/${workspace.id}/menu/counts`, () =>
    HttpResponse.json({ ...noMenuCounts, ...counts }),
  );
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

export const noTaskCounts = {
  mine: 0,
  overdue: 0,
  within_week: 0,
  needs_date: 0,
  to_pick_up: 0,
  waiting: 0,
  snoozed: 0,
};

/**
 * Two workspaces: `/` shows the list instead of going straight to the inbox. With one
 * workspace the "Werkruimtes" page is only a moment on the way to the inbox, so a test
 * that waits for it races the redirect.
 */
export function twoWorkspaces() {
  return http.get(`*${API}/workspaces`, () => HttpResponse.json([workspace, secondWorkspace]));
}

/**
 * Everything a workspace page asks besides its own content, for any workspace (the second
 * one of `twoWorkspaces`, a switch test): counts, sight, persons, labels, members, AI.
 * `documents`: what every document list of that workspace answers.
 */
export function workspaceHandlers(
  id: string,
  documents: components["schemas"]["DocumentSummaryOut"][] = [],
) {
  const base = `*${API}/workspaces/${id}`;
  return [
    http.get(`${base}/inbox/counts`, () => HttpResponse.json({ new: 0, review: 0, unassigned: 0 })),
    http.get(`${base}/my-tasks/counts`, () => HttpResponse.json(noTaskCounts)),
    http.get(`${base}/visibility`, () => HttpResponse.json(homeVisibility)),
    http.get(`${base}/documents`, () => HttpResponse.json(documents)),
    http.get(`${base}/persons`, () => HttpResponse.json([])),
    http.get(`${base}/people`, () => HttpResponse.json([])),
    http.get(`${base}/members`, () => HttpResponse.json([])),
    http.get(`${base}/labels`, () => HttpResponse.json([])),
    http.get(`${base}/ai`, () => HttpResponse.json({ mode: "off" })),
    http.get(`${base}/menu/counts`, () => HttpResponse.json(noMenuCounts)),
  ];
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

/** The filters of a `listArchive` body as search params: what a test asserts on. Empty,
 * false, zero and null values are left out (as an unset filter). */
export async function bodyFilters(request: Request): Promise<URLSearchParams> {
  const body = (await request.json()) as Record<string, unknown>;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(body)) {
    if (value === null || value === "" || value === false || value === 0) continue;
    params.set(key, String(value));
  }
  return params;
}

/** What `getArchiveFacets` answers for the one letter of `documentSummary`. */
export const archiveFacets: components["schemas"]["ArchiveFacetsOut"] = {
  kinds: [{ value: "letter", count: 1 }],
  types: [{ value: "assessment", count: 1 }],
  years: [{ value: "2026", count: 1 }],
  senders: [{ value: "Belastingdienst", count: 1 }],
};

/** An own document (archive): a title, an own document's type, an end date. */
export const ownDocument = {
  id: "9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b7777",
  client_id: "3c2b1a09-8f7e-4d6c-9b5a-4e3d2c1b0777",
  created_at: "2026-10-01T09:30:00Z",
  document_type: "identity",
  letter_date: "2025-06-01",
  page_count: 1,
  sender: "",
  status: "done",
  addressee_ids: [],
  label_ids: [],
  inbox_state: "",
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
  unread: false,
  letter_status: "",
  overdue: false,
  open_task_count: 0,
  kind: "document",
  title: "Paspoort",
  expires_on: "2031-03-12",
  remind_days: 30,
  retain_until: null,
  reminder_task_id: null,
};

/** What `countLetterStatuses` answers when nothing is counted. */
export const noLetterCounts = { new: 0, open: 0, waiting: 0, handled: 0, overdue: 0 };

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
  unread: false,
  letter_status: "",
  overdue: false,
  open_task_count: 0,
  kind: "letter",
  title: "",
  expires_on: null,
  remind_days: 30,
  retain_until: null,
  reminder_task_id: null,
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
      can_take: true,
      can_hand_over: true,
      can_release: false,
      wait_over: false,
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
    http.get(`*${API}/workspaces/${workspace.id}/people`, () => HttpResponse.json(people)),
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
    http.get(`*${API}/workspaces/${workspace.id}/documents/status-counts`, () =>
      HttpResponse.json(noLetterCounts),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/documents/archive-facets`, () =>
      HttpResponse.json(archiveFacets),
    ),
    // Archief's list is a POST (a sender never goes into a URL).
    http.post(`*${API}/workspaces/${workspace.id}/documents/archive`, () =>
      HttpResponse.json([documentSummary]),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/documents/${documentSummary.id}/events`, () =>
      HttpResponse.json([]),
    ),
    // Opening a letter marks it read (spec S 5.3).
    http.post(
      `*${API}/workspaces/${workspace.id}/documents/:documentId/read`,
      () => new HttpResponse(null, { status: 204 }),
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
    warning: "",
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
  scope: "mine" | "all" = "mine",
): components["schemas"]["MyExportOut"] {
  return {
    allowed,
    status,
    document_count: 0,
    size: 0,
    scope,
    failure_code: "",
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
    // Mensen shows the same members, each as an active person.
    http.get(`*${API}/workspaces/${workspace.id}/people`, () =>
      HttpResponse.json(list.map(memberAsPerson)),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/invitations`, () =>
      HttpResponse.json(invitations),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/my-export`, () => HttpResponse.json(myExport)),
  ];
}

/** A member as Mensen lists them: an active person with their account. */
export function memberAsPerson(
  member: components["schemas"]["MemberOut"],
): components["schemas"]["PersonAccountOut"] {
  return personAccount({
    id: member.person_id ?? `p-${member.user_id}`,
    display_name: member.name,
    state: "active",
    user_id: member.user_id,
    role: member.role,
    sorts_post: member.sorts_post ?? false,
    department_ids: member.department_ids ?? [],
    can_manage_account: member.can_manage_account ?? false,
  });
}

type PersonAccount = components["schemas"]["PersonAccountOut"];

/** A person on Mensen as an admin sees them; every account fact empty unless given. */
export function personAccount(
  overrides: Partial<PersonAccount> & { id: string; display_name: string },
): PersonAccount {
  return {
    name_variants: [],
    archived: false,
    document_count: 0,
    state: "inactive",
    user_id: null,
    role: "",
    sorts_post: false,
    department_ids: [],
    email: "",
    last_sign_in_at: null,
    last_app_use_at: null,
    deactivated_at: null,
    invitation_id: null,
    invitation_role: "",
    invitation_expires_at: null,
    invitation_expired: false,
    can_manage_account: false,
    activity_hidden: false,
    ...overrides,
  };
}

/** Anna (the caller, admin), Bram (member, never signed in), Papa (no account), Oma
 * (invited), Kees (switched off): one person in every state. */
export const people: PersonAccount[] = [
  personAccount({
    id: annaPerson.id,
    display_name: "Anna",
    state: "active",
    user_id: user.id,
    role: "admin",
    email: user.email,
    last_sign_in_at: "2026-11-20T08:15:00Z",
  }),
  personAccount({
    id: "a3333333-3333-4333-8333-333333333333",
    display_name: "Bram",
    state: "active",
    user_id: "b3333333-3333-4333-8333-333333333333",
    role: "member",
    email: "bram@example.nl",
    can_manage_account: true,
  }),
  personAccount({
    id: papa.id,
    display_name: "Papa",
    name_variants: ["P. de Vries"],
    document_count: 2,
  }),
  personAccount({
    id: "a4444444-4444-4444-8444-444444444444",
    display_name: "Oma",
    state: "invited",
    invitation_id: "e4444444-4444-4444-8444-444444444444",
    invitation_role: "member",
    invitation_expires_at: "2099-11-24T10:00:00Z",
  }),
  personAccount({
    id: "a5555555-5555-4555-8555-555555555555",
    display_name: "Kees",
    state: "deactivated",
    email: "kees@example.nl",
    last_sign_in_at: "2026-10-01T12:00:00Z",
    deactivated_at: "2026-11-01T12:00:00Z",
  }),
];

/** The same people as somebody who is no admin gets them: no account facts, active or not. */
export const peopleForMember: PersonAccount[] = people.map((person) =>
  personAccount({
    id: person.id,
    display_name: person.display_name,
    name_variants: person.name_variants,
    document_count: person.document_count,
    user_id: person.user_id,
    archived: person.archived,
    state: person.state === "active" ? "active" : "inactive",
  }),
);

/** What Mensen asks: the people, the persons (department names), invitations and export. */
export function peopleHandlers(
  list: PersonAccount[] = people,
  invitations: components["schemas"]["InvitationOut"][] = [],
) {
  return [
    http.get(`*${API}/workspaces/${workspace.id}/people`, () => HttpResponse.json(list)),
    http.get(`*${API}/workspaces/${workspace.id}/persons`, () =>
      HttpResponse.json([papa, annaPerson]),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/members`, () => HttpResponse.json(members)),
    http.get(`*${API}/workspaces/${workspace.id}/invitations`, () =>
      HttpResponse.json(invitations),
    ),
    http.get(`*${API}/workspaces/${workspace.id}/my-export`, () =>
      HttpResponse.json(exportState()),
    ),
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
