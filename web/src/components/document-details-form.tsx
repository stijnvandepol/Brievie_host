import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Button, Form } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { DOCUMENT_TYPES, formatLetterDate, isExpired, typeLabel } from "./document-table";
import { ErrorAlert } from "./page";

type Document = components["schemas"]["DocumentOut"];
type DetailsIn = components["schemas"]["DocumentDetailsIn"];

/** How many days before "Geldig tot" the reminder starts. */
const REMIND_DAYS = [7, 30, 60, 90] as const;

type Values = {
  title: string;
  documentType: string;
  date: string;
  expiresOn: string;
  remindDays: number;
  retainUntil: string;
};

function valuesOf(document: Document): Values {
  return {
    title: document.title ?? "",
    documentType: document.document_type,
    date: document.letter_date ?? "",
    expiresOn: document.expires_on ?? "",
    remindDays: document.remind_days ?? 30,
    retainUntil: document.retain_until ?? "",
  };
}

/** Only what changed (an empty text or a missing date means "unchanged"; a date is cleared
 * with its flag). A letter sends only "Bewaren tot". */
export function changedDetails(before: Values, after: Values, isDocument: boolean): DetailsIn {
  const body: DetailsIn = {
    title: "",
    document_type: "",
    document_date: null,
    expires_on: null,
    clear_expires_on: false,
    remind_days: 0,
    retain_until: null,
    clear_retain_until: false,
  };
  if (isDocument) {
    if (after.title.trim() && after.title !== before.title) body.title = after.title;
    if (after.documentType !== before.documentType) body.document_type = after.documentType;
    if (after.date && after.date !== before.date) body.document_date = after.date;
    if (after.expiresOn !== before.expiresOn) {
      if (after.expiresOn) body.expires_on = after.expiresOn;
      else body.clear_expires_on = true;
    }
    if (after.remindDays !== before.remindDays) body.remind_days = after.remindDays;
  }
  if (after.retainUntil !== before.retainUntil) {
    if (after.retainUntil) body.retain_until = after.retainUntil;
    else body.clear_retain_until = true;
  }
  return body;
}

/** Whether a permanent delete is refused today ("Bewaren tot" today or later). */
export function isRetained(retainUntil: string | null | undefined): boolean {
  return Boolean(retainUntil) && !isExpired(retainUntil ?? "");
}

function useSaveDetails(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: DetailsIn) => {
      const { data, error } = await api.PATCH(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/details",
        {
          params: { path: { workspace_id: workspaceId, document_id: documentId } },
          body,
        },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: async (document) => {
      queryClient.setQueryData(["workspaces", workspaceId, "documents", documentId], document);
      await queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId] });
    },
  });
}

/**
 * The details of an own document (archive spec S 11.3): title, type, date, "Geldig tot" with
 * its reminder, "Bewaren tot". For a letter only "Bewaren tot". An editor changes them; a
 * read-only member (or one who reads through emergency access) sees them.
 */
export function DocumentDetails({
  workspaceId,
  document,
  canEdit,
}: {
  workspaceId: string;
  document: Document;
  canEdit: boolean;
}) {
  const isDocument = document.kind === "document";
  const before = valuesOf(document);
  const [values, setValues] = useState<Values>(before);
  const [editing, setEditing] = useState(false);
  const save = useSaveDetails(workspaceId, document.id);
  const ids = {
    title: useId(),
    type: useId(),
    date: useId(),
    expires: useId(),
    remind: useId(),
    retain: useId(),
  };
  const set = (change: Partial<Values>) => setValues((old) => ({ ...old, ...change }));

  if (!editing) {
    const ends = document.expires_on;
    const kept = document.retain_until;
    return (
      <>
        <dl className="detail-list">
          {isDocument ? (
            <>
              <div>
                <dt>{t`Titel`}</dt>
                <dd>{document.title}</dd>
              </div>
              <div>
                <dt>{t`Type`}</dt>
                <dd>{typeLabel(document.document_type)}</dd>
              </div>
              <div>
                <dt>{t`Datum`}</dt>
                <dd>{document.letter_date ? formatLetterDate(document.letter_date) : ""}</dd>
              </div>
              <div>
                <dt>{t`Geldig tot`}</dt>
                <dd>
                  {ends
                    ? isExpired(ends)
                      ? t`Verlopen op ${formatLetterDate(ends)}`
                      : t`${formatLetterDate(ends)}, herinnering ${document.remind_days} dagen vooraf`
                    : t`Geen datum`}
                </dd>
              </div>
            </>
          ) : null}
          <div>
            <dt>{t`Bewaren tot`}</dt>
            <dd>{kept ? formatLetterDate(kept) : t`Geen datum`}</dd>
          </div>
        </dl>
        {canEdit ? (
          <div className="panel-actions">
            <Button
              className="button-text"
              onPress={() => {
                setValues(valuesOf(document));
                setEditing(true);
              }}
            >
              {isDocument ? t`Gegevens aanpassen` : t`Bewaren tot aanpassen`}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <Form
      className="form"
      aria-label={isDocument ? t`Gegevens van het document` : t`Bewaren tot aanpassen`}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(changedDetails(before, values, isDocument), {
          onSuccess: () => setEditing(false),
        });
      }}
    >
      {isDocument ? (
        <>
          <div className="field">
            <label htmlFor={ids.title}>{t`Titel`}</label>
            <input
              id={ids.title}
              type="text"
              maxLength={200}
              required
              value={values.title}
              onChange={(event) => set({ title: event.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={ids.type}>{t`Type`}</label>
            <select
              id={ids.type}
              value={values.documentType}
              onChange={(event) => set({ documentType: event.target.value })}
            >
              {DOCUMENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {typeLabel(type)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={ids.date}>{t`Datum`}</label>
            <input
              id={ids.date}
              type="date"
              value={values.date}
              onChange={(event) => set({ date: event.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={ids.expires}>{t`Geldig tot`}</label>
            <input
              id={ids.expires}
              type="date"
              value={values.expiresOn}
              onChange={(event) => set({ expiresOn: event.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={ids.remind}>{t`Herinner me vooraf`}</label>
            <select
              id={ids.remind}
              value={String(values.remindDays)}
              onChange={(event) => set({ remindDays: Number(event.target.value) })}
            >
              {REMIND_DAYS.map((days) => (
                <option key={days} value={String(days)}>
                  {t`${days} dagen vooraf`}
                </option>
              ))}
            </select>
          </div>
        </>
      ) : null}
      <div className="field">
        <label htmlFor={ids.retain}>{t`Bewaren tot`}</label>
        <input
          id={ids.retain}
          type="date"
          value={values.retainUntil}
          onChange={(event) => set({ retainUntil: event.target.value })}
        />
        <p className="muted small">{t`Tot deze datum kan niemand het voorgoed wissen. Leeg: geen datum.`}</p>
      </div>
      <ErrorAlert error={save.error} />
      <div className="form-actions">
        <Button type="submit" className="button" isDisabled={save.isPending}>
          {t`Opslaan`}
        </Button>
        <Button className="button secondary" onPress={() => setEditing(false)}>
          {t`Annuleren`}
        </Button>
      </div>
    </Form>
  );
}
