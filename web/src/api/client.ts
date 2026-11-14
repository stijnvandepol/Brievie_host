import { t } from "@lingui/core/macro";
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type ErrorBody = components["schemas"]["ErrorOut"];

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function readCookie(name: string): string | undefined {
  for (const part of document.cookie.split("; ")) {
    const [key, ...value] = part.split("=");
    if (key === name) return decodeURIComponent(value.join("="));
  }
  return undefined;
}

/** Django wants the CSRF cookie echoed in a header on every session-authenticated write. */
const csrf: Middleware = {
  onRequest({ request }) {
    const token = readCookie("csrftoken");
    if (token && !SAFE_METHODS.has(request.method)) {
      request.headers.set("X-CSRFToken", token);
    }
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: globalThis.location.origin,
  credentials: "include",
  // Looked up per call, so a test double installed after import is used.
  fetch: (request) => globalThis.fetch(request),
});
api.use(csrf);

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    "message" in value &&
    typeof value.code === "string" &&
    typeof value.message === "string"
  );
}

/** The server's own Dutch sentence when there is one, otherwise a calm fallback. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiFailure || isErrorBody(error)) return error.message;
  if (error instanceof TypeError) {
    return t`We kunnen de server niet bereiken. Controleer je verbinding en probeer het opnieuw.`;
  }
  return t`Er is iets misgegaan. Probeer het later opnieuw.`;
}

/** An error answer of the API: `message` is for the user, `code` is for branching. */
export class ApiFailure extends Error {
  readonly code: string | undefined;

  constructor(error: unknown) {
    super(errorMessage(error));
    this.code = isErrorBody(error) ? error.code : undefined;
  }
}
