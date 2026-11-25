import { t } from "@lingui/core/macro";
import { useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useId, useRef, useState } from "react";
import { Button } from "react-aria-components";
import { api, errorMessage } from "../api/client";
import { DOCUMENT_TYPES, typeLabel } from "./document-table";

/** Intake spec S 2: at most 50 files chosen at once, three requests at a time. */
export const MAX_FILES = 50;
export const PARALLEL = 3;
const ACCEPT = "application/pdf,image/jpeg,image/png";

type OwnFields = {
  kind: "document";
  title: string;
  document_type: string;
  expires_on?: string;
  retain_until?: string;
  private?: boolean;
};

type Item = {
  key: string;
  file: File;
  /** Kept on a retry: the server answers the same document for the same file. */
  clientId: string;
  own?: OwnFields;
  state: "waiting" | "busy" | "done" | "failed";
  message: string;
};

/** One file to the server; the server's own sentence when it refuses. */
async function send(workspaceId: string, item: Item): Promise<void> {
  const metadata = JSON.stringify({ client_id: item.clientId, ...(item.own ?? {}) });
  const { error } = await api.POST("/api/v1/workspaces/{workspace_id}/documents/files", {
    params: { path: { workspace_id: workspaceId } },
    body: { metadata, file: "" },
    bodySerializer: () => {
      const form = new FormData();
      form.append("metadata", metadata);
      form.append("file", item.file, item.file.name);
      return form;
    },
  });
  if (error !== undefined) throw error;
}

/**
 * "Brief toevoegen" and "Document toevoegen" (intake spec S 2, archive W3): PDFs and photos
 * from this computer or phone become letters, or one own document with its archive fields.
 * One request per file, three at a time, a line per file. Not shown to a read-only member.
 */
export function UploadDocuments({ workspaceId, role }: { workspaceId: string; role: string }) {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [refusal, setRefusal] = useState("");
  const [dragging, setDragging] = useState(false);
  const [ownOpen, setOwnOpen] = useState(false);
  const letters = useRef<HTMLInputElement>(null);
  const running = useRef(0);
  const queue = useRef<Item[]>([]);

  if (role === "viewer") return null;

  const update = (key: string, change: Partial<Item>) =>
    setItems((current) =>
      current.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );

  const pump = () => {
    while (running.current < PARALLEL && queue.current.length > 0) {
      const item = queue.current.shift() as Item;
      running.current += 1;
      update(item.key, { state: "busy", message: t`Bezig met uploaden…` });
      send(workspaceId, item)
        .then(() => {
          update(item.key, {
            state: "done",
            message: item.own
              ? t`Toegevoegd. Brievie leest het document nu.`
              : t`Toegevoegd. Brievie leest de brief nu.`,
          });
          void queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId] });
        })
        .catch((error: unknown) =>
          update(item.key, { state: "failed", message: errorMessage(error) }),
        )
        .finally(() => {
          running.current -= 1;
          pump();
        });
    }
  };

  const start = (added: Item[]) => {
    setItems((current) => [...current, ...added]);
    queue.current.push(...added);
    pump();
  };

  const addFiles = (files: File[], own?: OwnFields) => {
    setRefusal("");
    if (files.length === 0) return;
    if (files.length > MAX_FILES) {
      setRefusal(t`Kies maximaal 50 bestanden tegelijk.`);
      return;
    }
    start(
      files.map((file) => ({
        key: crypto.randomUUID(),
        file,
        clientId: crypto.randomUUID(),
        own,
        state: "waiting",
        message: t`Wacht…`,
      })),
    );
  };

  const retry = (item: Item) => {
    const again = { ...item, state: "waiting" as const, message: t`Wacht…` };
    update(item.key, again);
    queue.current.push(again);
    pump();
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    addFiles([...event.dataTransfer.files]);
  };

  return (
    <section
      className={dragging ? "upload-zone upload-zone-active" : "upload-zone"}
      aria-label={t`Toevoegen`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="upload-commands">
        <Button className="button" onPress={() => letters.current?.click()}>
          {t`Brief toevoegen`}
        </Button>
        <Button className="button" onPress={() => setOwnOpen((open) => !open)}>
          {t`Document toevoegen`}
        </Button>
        <input
          ref={letters}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          data-testid="letter-files"
          onChange={(event) => {
            addFiles([...(event.target.files ?? [])]);
            event.target.value = "";
          }}
        />
      </div>
      <p className="muted small">
        {t`Sleep PDF's of foto's hierheen.`}{" "}
        {t`Eén bestand wordt één brief. Zitten er meer brieven in één bestand? Scan of bewaar ze dan los.`}
      </p>
      {ownOpen ? (
        <OwnDocumentForm
          onSubmit={(file, own) => {
            setOwnOpen(false);
            addFiles([file], own);
          }}
        />
      ) : null}
      {refusal ? (
        <p role="alert" className="error-text">
          {refusal}
        </p>
      ) : null}
      {items.length > 0 ? (
        <ul className="upload-list" aria-label={t`Bestanden`}>
          {items.map((item) => (
            <li key={item.key}>
              <span className="upload-name">{item.file.name}</span>{" "}
              <span role={item.state === "failed" ? "alert" : "status"}>{item.message}</span>
              {item.state === "failed" ? (
                <Button className="button-text" onPress={() => retry(item)}>
                  {t`Opnieuw proberen`}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** An own document: title, type and the archive's dates, then one file. */
function OwnDocumentForm({ onSubmit }: { onSubmit: (file: File, own: OwnFields) => void }) {
  const ids = {
    title: useId(),
    type: useId(),
    expires: useId(),
    retain: useId(),
    own: useId(),
    file: useId(),
  };
  const [title, setTitle] = useState("");
  const [documentType, setDocumentType] = useState<string>(DOCUMENT_TYPES[0]);
  const [expiresOn, setExpiresOn] = useState("");
  const [retainUntil, setRetainUntil] = useState("");
  const [onlyMe, setOnlyMe] = useState(false);
  const [file, setFile] = useState<File | null>(null);

  return (
    <form
      className="upload-own"
      aria-label={t`Document toevoegen`}
      onSubmit={(event) => {
        event.preventDefault();
        if (!file || !title.trim()) return;
        onSubmit(file, {
          kind: "document",
          title: title.trim(),
          document_type: documentType,
          ...(expiresOn ? { expires_on: expiresOn } : {}),
          ...(retainUntil ? { retain_until: retainUntil } : {}),
          // Absent: the server makes the sensitive types private (archive Besluit B3).
          ...(onlyMe ? { private: true } : {}),
        });
      }}
    >
      <div className="field">
        <label htmlFor={ids.title}>{t`Titel`}</label>
        <input
          id={ids.title}
          required
          maxLength={200}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={ids.type}>{t`Type`}</label>
        <select
          id={ids.type}
          value={documentType}
          onChange={(event) => setDocumentType(event.target.value)}
        >
          {DOCUMENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {typeLabel(type)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={ids.expires}>{t`Geldig tot`}</label>
        <input
          id={ids.expires}
          type="date"
          value={expiresOn}
          onChange={(event) => setExpiresOn(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={ids.retain}>{t`Bewaren tot`}</label>
        <input
          id={ids.retain}
          type="date"
          value={retainUntil}
          onChange={(event) => setRetainUntil(event.target.value)}
        />
      </div>
      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={onlyMe}
            onChange={(event) => setOnlyMe(event.target.checked)}
          />{" "}
          {t`Alleen voor mij`}
        </label>
      </div>
      <div className="field">
        <label htmlFor={ids.file}>{t`Bestand`}</label>
        <input
          id={ids.file}
          type="file"
          accept={ACCEPT}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </div>
      <button type="submit" className="button" disabled={!file || !title.trim()}>
        {t`Toevoegen`}
      </button>
    </form>
  );
}
