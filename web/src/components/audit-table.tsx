import { t } from "@lingui/core/macro";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button } from "react-aria-components";
import type { components } from "../api/schema";
import { actorKindLabel, auditLabel, targetKindLabel } from "./audit-codes";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";
import { EmptyState, Loading } from "./ui";

export type AuditEvent = components["schemas"]["AuditEventOut"];

/** What the filter bar sets. Empty strings mean "no filter". */
export type AuditFilters = { userId: string; code: string; since: string; until: string };

/** The rows the server sends at once by default; a shorter page is the last one. */
const PAGE_SIZE = 100;

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium", timeStyle: "medium" });

/** The query string of the filters; `before` only for the next page of the list, never for the CSV. */
export function auditParams(filters: AuditFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.userId) params.set("user_id", filters.userId);
  if (filters.code) params.set("code", filters.code);
  if (filters.since) params.set("since", filters.since);
  if (filters.until) params.set("until", filters.until);
  return params;
}

function Row({ event }: { event: AuditEvent }) {
  return (
    <tr>
      <td className="num">
        <time dateTime={event.created_at}>{dateTime.format(new Date(event.created_at))}</time>
      </td>
      <td>{event.actor_name || actorKindLabel(event.actor_kind)}</td>
      <th scope="row" className="cell-strong">
        {auditLabel(event.code, event.label)}
      </th>
      <td>{event.target_name || targetKindLabel(event.target_kind)}</td>
      <td className="num">{event.client_ip}</td>
    </tr>
  );
}

/**
 * A log with its filters: who, what, from and until, newest first, "Meer laden" for the next
 * page (the last row's time is `before`) and a CSV link with the same filters. The CSV is an
 * ordinary link: the browser sends the session cookie and no token is involved.
 */
export function AuditLog({
  scopeKey,
  fetchPage,
  csvPath,
  codes,
  people,
}: {
  scopeKey: readonly string[];
  fetchPage: (filters: AuditFilters, before: string | undefined) => Promise<AuditEvent[]>;
  csvPath: string;
  /** The event codes this log can hold: its own scope, not the whole list. */
  codes: readonly string[];
  /** The accounts one can filter on; none where the server has no such list. */
  people?: { user_id: string; name: string }[];
}) {
  const id = useId();
  const [filters, setFilters] = useState<AuditFilters>({
    userId: "",
    code: "",
    since: "",
    until: "",
  });
  const set = (change: Partial<AuditFilters>) => setFilters({ ...filters, ...change });

  const log = useInfiniteQuery({
    queryKey: [...scopeKey, "audit", filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => fetchPage(filters, pageParam),
    getNextPageParam: (last) =>
      last.length >= PAGE_SIZE ? last[last.length - 1]?.created_at : undefined,
  });
  const events = log.data?.pages.flat() ?? [];
  const query = auditParams(filters).toString();
  const csv = query ? `${csvPath}?${query}` : csvPath;

  return (
    <>
      <div className="filter-bar">
        {people ? (
          <div className="filter">
            <label htmlFor={`${id}-person`}>{t`Persoon`}</label>
            <select
              id={`${id}-person`}
              value={filters.userId}
              onChange={(event) => set({ userId: event.target.value })}
            >
              <option value="">{t`Iedereen`}</option>
              {people.map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="filter">
          <label htmlFor={`${id}-code`}>{t`Gebeurtenis`}</label>
          <select
            id={`${id}-code`}
            value={filters.code}
            onChange={(event) => set({ code: event.target.value })}
          >
            <option value="">{t`Alle gebeurtenissen`}</option>
            {codes.map((code) => (
              <option key={code} value={code}>
                {auditLabel(code, code)}
              </option>
            ))}
          </select>
        </div>
        <div className="filter">
          <label htmlFor={`${id}-since`}>{t`Van`}</label>
          <input
            id={`${id}-since`}
            type="date"
            value={filters.since}
            max={filters.until || undefined}
            onChange={(event) => set({ since: event.target.value })}
          />
        </div>
        <div className="filter">
          <label htmlFor={`${id}-until`}>{t`Tot`}</label>
          <input
            id={`${id}-until`}
            type="date"
            value={filters.until}
            min={filters.since || undefined}
            onChange={(event) => set({ until: event.target.value })}
          />
        </div>
        <a className="button secondary" href={csv}>
          <Icon name="document" />
          {t`Download als CSV`}
        </a>
      </div>
      {log.isPending ? <Loading /> : null}
      <ErrorAlert error={log.error} />
      {log.data && events.length === 0 ? (
        <EmptyState icon="document" hint={t`Pas de filters aan om andere gebeurtenissen te zien.`}>
          {t`Geen gebeurtenissen gevonden.`}
        </EmptyState>
      ) : null}
      {events.length > 0 ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Logboek`} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Tijdstip`}</th>
                <th scope="col">{t`Wie`}</th>
                <th scope="col">{t`Gebeurtenis`}</th>
                <th scope="col">{t`Om wie of wat`}</th>
                <th scope="col">{t`Adres`}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <Row key={event.id} event={event} />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {log.hasNextPage ? (
        <div>
          <Button
            className="button secondary"
            isPending={log.isFetchingNextPage}
            onPress={() => log.fetchNextPage()}
          >
            {t`Meer laden`}
          </Button>
        </div>
      ) : null}
    </>
  );
}
