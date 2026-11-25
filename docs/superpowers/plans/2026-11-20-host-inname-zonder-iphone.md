# Intake without an iPhone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A member adds a PDF, JPEG or PNG from any browser, and (opt-in) an office scanner drops files in a folder on the NAS, optionally in a subfolder per person or department that makes them the addressee; both become letters through the same pipeline as an app scan.

**Architecture:** A new app `apps.intake` checks a file (pure), stores the original sealed and queues `process_document`; a new first step in that job renders pages (PDFium, Pillow) and text (PDF text layer or Tesseract) before the unchanged `run_pipeline`. The scan folder is a separate `intake` service (same image, queue `intake`) that alone mounts the share; every file operation on the share goes through one guarded module.

**Tech Stack:** Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), Procrastinate; new: `pypdfium2`, `Pillow`, Debian `tesseract-ocr` + `tesseract-ocr-nld`; React + TanStack + React Aria + Lingui (web).

**Spec:** `docs/superpowers/specs/2026-11-20-inname-zonder-iphone-design.md` (read it first; its "Besluit" lines bind).

**Phases.** Phase 0 is a gate: if Task 0 fails, stop and ask the owner before any other task. Phase 1 (Tasks 1-8) is web upload end to end and merges on its own. Phase 2 (Tasks 9-15) is the scan folder, off by default. No e-mail intake.

**Naming (decided here, deviates from the spec).** In this codebase "inbox" already means the "Te doen" page (route `inbox`, `GET /workspaces/{id}/inbox/counts`, `inbox_state`, `docs/GLOSSARY.md`). The scan folder therefore uses `scan` everywhere: env `BRIEVIE_SCAN_DIR` / `BRIEVIE_SCAN_DIR_SET` / `BRIEVIE_SCAN_GID`, settings `BRIEVIE_SCAN_ROOT` (`/scan`) / `BRIEVIE_SCAN_STABLE_SECONDS`, volume `scan-unset`, modules `apps/intake/scan_folder.py` (job) and `apps/intake/scan_files.py` (the guarded file code, the spec's `inbox_files.py`), models `ScanFolderSettings` / `ScanFolder` / `ScanFile`, endpoint `/workspaces/{id}/scan-folder` (`getScanFolder`, `updateScanFolder`), errors `scan_folder_taken` / `scan_folder_unavailable`, audit settings `scan_folder` / `scan_folder_per_person`, web route `scan-folder`. The Dutch word stays "Scanmap"; the history origin stays `watch_folder` (matches the existing `Document.Source.WATCH_FOLDER`).

## Global Constraints

- Read `CLAUDE.md` "Rules in short"; all of it applies. In particular:
- Every new table: UUID `id`, `created_at`, `updated_at`; workspace content tables subclass `WorkspaceModel` and get RLS enabled **and forced** (`apps.workspaces.rls.enable_rls` in the migration, as `documents/migrations/0008_memberexport.py`).
- API only under `/api/v1`, additive: every field added to an existing schema has a default; nullable fields are primitives only, nullable inputs have a default; no `anyOf` (the contract test compares with `backend/tests/fixtures/openapi_1b.json` and `openapi_1c.json`). Every endpoint: typed schemas, explicit camelCase `operation_id`; errors `{"code", "message"}` via `ApiError` with a Dutch B1 sentence.
- User-facing text Dutch (B1): backend through `gettext`, web through Lingui `t` macros, then `pnpm extract`. Code, comments, commits in English.
- Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored, logged or sent to an AI service; this includes `original_name`. Files are sealed (`apps.common.crypto.seal`, key `apps.workspaces.keys.workspace_key`) before they touch disk; the original too.
- No new dependency without a line in `docs/DECISIONS.md` (Task 1 writes them for `pypdfium2`, `Pillow`, Tesseract).
- Jobs take ids only, enter `workspace_context` themselves, short transactions, none open during rendering, OCR or a read from the share.
- Automatic changes have no actor: a scan-folder letter has `scanned_by=None`; a preset addressee (person or department, spec 3.2a) is added with `actor=None`, origin `watch_folder`, `automatic=True`, through `organise.add_addressee` inside `apps.documents.visibility.addressee_scope(document)` in the transaction that creates the letter (so `addressee_ids` and reader rows are synced before anyone can read it). Only `apps.documents.organise` adds addressees. Never a `Person` created from a folder name.
- No document content, file names, tokens or BSNs in logs: codes and document ids only.
- Every mutating service function in `transaction.atomic`; thin endpoints call one service function.
- `mypy --strict`, `ruff check`, `ruff format --check` clean; no `Any`, no bare `except`.
- Builders never run `docker`, `./brievie`, `make dev` or `make test` on the Mac. Database tests run in CI; locally run only tests that need no database (pure modules, `scan_files.py` in `tmp_path`), plus `make lint typecheck`, `pnpm test lint typecheck`.
- Code that removes, moves or creates files on the share never runs outside pytest's `tmp_path` or a CI container. No absolute real paths in tests (no `/volume1`, no home directories); never touch the test server `brievie`; never print `deploy/.env`; no push, no tags.
- Exact values: `MAX_IMAGE_PIXELS = 40_000_000`; render 200 dpi, longest side ≤ 2500 px, JPEG quality 85; text layer used when a page has ≥ 20 non-space characters; OCR `nld+eng`, 60 s per page (`BRIEVIE_OCR_TIMEOUT_SECONDS`); PDF page count in the API ≤ 5 s; `original_name` ≤ 200 characters; stable after 60 s (`BRIEVIE_SCAN_STABLE_SECONDS`); ≤ 20 files per workspace per round; imported `ScanFile` rows purged after 30 days; person folder name ≤ 60 characters; browser batch ≤ 50 files, ≤ 3 uploads at a time.
- Conventional commits, each ending with the line `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Review Focus

- A letter whose AI call failed and is retried (`RetryableError`) must not be rendered again or get its pages twice → Task 5 `test_retry_after_prepare_keeps_pages`.
- A phone photo uploaded from an Android browser with EXIF orientation 6 must come out upright → Task 3 `test_jpeg_exif_orientation_applied`.
- A file the admin puts back from `mislukt/` into the folder must be tried again, not ignored because of its old `failed` row → Task 12 `test_file_put_back_from_failed_is_tried_again`.
- Two persons "Anne" and "anne": SMB is case-insensitive, so they must get different folders (`anne`, `anne (2)`) → Task 11 `test_folder_names_unique_ignoring_case`.
- A directory in the workspace folder that maps to nobody must never be read, and nothing in it moved or removed; it shows on the page with its file count → Task 12 `test_unknown_subfolder_is_left_alone`.
- A department folder while departments are off is paused, not failed: its files stay and arrive when departments go back on → Task 12 `test_department_folder_paused_while_departments_off`.
- A folder's addressee is on the letter before the pipeline runs and is synced into `addressee_ids` in the same transaction; with "Iedereen ziet alle post" off, only that person (and the admins while they read all) sees the letter, and no `DocumentReader` row comes from the folder → Task 12 `test_person_folder_letter_seen_by_that_person_only`.
- A zero-byte file, or a browser name like `C:\fakepath\brief.pdf`: 422 `file_type_not_supported` (not 500), and only `brief.pdf` stored → Task 2 `test_empty_file_refused` and `test_original_name_base_only`.

---

## Phase 0: the gate

### Task 0: Measure Tesseract on synthetic letters

**Files:**
- Create: `tools/ocr-gate/letters.json`, `tools/ocr-gate/make_letters.py`, `tools/ocr-gate/measure.py`, `tools/ocr-gate/test_measure.py`
- Modify: `.github/workflows/ci.yml` (new job `ocr-gate`)

**Interfaces — Produces:** nothing the product imports. The job is the record; its log table goes into the Task 1 DECISIONS line.

Decisions:
- `letters.json`: 12 synthetic Dutch letters, each `{id, sender, date, deadline, body}` with fictional senders only (e.g. "Gemeente Voorbeeldstad", "Waterschap Lage Velden", "Energie Noord BV"; no real organisations, no real names), dates written in mixed forms (`12 november 2026`, `12-11-2026`, `12/11/2026`), at least 4 invoices, 2 letters with a table, 2 with small print (8 pt). No 9-digit numbers at all (so no BSN-shaped strings).
- `make_letters.py PATH_OUT`: Pillow + `fonts-dejavu-core`, A4 at 200 dpi (1654 × 2339 px, the pipeline's render size). Two variants per letter: `clean` and `scan` (rotate 0.8°, Gaussian blur radius 0.6, light grey noise, JPEG quality 70). Deterministic (`random.Random(letter id)`). Images are generated in the job, not committed.
- `measure.py IMAGES_DIR letters.json`: runs `tesseract stdin stdout -l nld+eng` per image (`subprocess.run`, no shell, timeout 60 s), extracts facts, prints a table per letter and variant, exits 1 below the threshold.
- Fact matching: sender found when a line of the OCR text, whitespace-normalised and case-folded, has `difflib.SequenceMatcher.ratio() ≥ 0.9` with the sender; a date fact found when the expected date is among all dates parsed from the text (formats `d maand yyyy` with Dutch month names, `dd-mm-yyyy`, `dd/mm/yyyy`, `d-m-yyyy`).
- **Threshold (pass):** `clean`: at least 35 of 36 facts and all 12 deadlines; `scan`: at least 33 of 36 facts (≥ 90 %) and at least 11 of 12 deadlines. A deadline missing from the text cannot be recovered by the AI later, and missing one costs the user money; VisionKit on the phone reads clean print almost perfectly, so server OCR must be close on clean pages and still useful on a poor scan.
- **If the job fails: stop.** Report the table to the owner; options for the owner are a higher render dpi, PaddleOCR/docling behind the same interface, or accepting the measured quality. No other task starts before the owner decides.
- CI job `ocr-gate`: `runs-on: ubuntu-latest`, `container: python:3.13-slim` (the Dockerfile's base, so the same Debian Tesseract), apt `tesseract-ocr tesseract-ocr-nld fonts-dejavu-core`, `pip install Pillow==<current release, pinned>` in the job container only (a CI tool here; Task 1 makes it a product dependency with its DECISIONS line); then `python -m unittest tools/ocr-gate/test_measure.py`, `make_letters.py`, `measure.py`.

- [ ] **Step 1: Write the failing test** `tools/ocr-gate/test_measure.py` (unittest, no Tesseract): `test_dates_parsed_in_all_forms` (`parse_dates("vóór 3 december 2026 en 05-01-2027") == {date(2026,12,3), date(2027,1,5)}`); `test_sender_fuzzy_match` (`"Gemeente Voorbeeldstad"` found in `"GEMEENTE V0orbeeldstad\n..."`, not found in `"Gemeente Andersdorp"`); `test_threshold` (`passes(clean=(35, 12), scan=(33, 11)) is True`, `passes(clean=(36, 11), scan=(36, 12)) is False`, `passes(clean=(36, 12), scan=(32, 12)) is False`); `test_letters_have_no_nine_digit_numbers` (no `\d{9}` after removing separators in any field of `letters.json`).
- [ ] **Step 2: Run** `python3 -m unittest tools/ocr-gate/test_measure.py` → FAIL (`measure` has no `parse_dates`).
- [ ] **Step 3: Implement** in `measure.py`: `parse_dates(text: str) -> set[date]`, `sender_found(text: str, sender: str) -> bool`, `passes(*, clean: tuple[int, int], scan: tuple[int, int]) -> bool` (facts found, deadlines found); `make_letters.py`; `letters.json`; the CI job.
- [ ] **Step 4: Run** the unit tests locally → PASS. Push is not yours: the controller pushes the branch and reads the `ocr-gate` job; PASS → Phase 1, FAIL → owner.
- [ ] **Step 5: Commit** `test(intake): measure Tesseract on synthetic letters before building on it`.

---

## Phase 1: web upload

### Task 1: Dependencies, original fields, settings

**Files:**
- Modify: `backend/pyproject.toml` (+ `uv.lock` via `uv add`), `deploy/Dockerfile` (runtime stage: `tesseract-ocr tesseract-ocr-nld`), `docs/DECISIONS.md`, `backend/apps/documents/models.py`, `backend/apps/documents/intake.py`, `backend/brievie/settings.py`, `backend/apps/pipeline/jobs.py`, `backend/tests/test_deploy_files.py`
- Create: `backend/apps/intake/__init__.py`, `apps.py` (add to `INSTALLED_APPS`), one `documents` migration
- Test: `backend/tests/test_intake_models.py`

**Interfaces — Produces:**
- `Document.OriginalKind(TextChoices)`: `NONE = ""`, `PDF = "pdf"`, `JPEG = "jpeg"`, `PNG = "png"`; fields `original_kind` (default `""`), `original_sha256: CharField(64, blank)`, `original_size: PositiveBigIntegerField(null)`, `original_name: CharField(200, blank)`; index `(workspace, original_sha256)`.
- In `apps/documents/intake.py`: `original_path(workspace_id: UUID, document_id: UUID) -> str` = `"<ws>/<doc>/original"`; `original_aad(workspace_id: UUID, document_id: UUID) -> bytes` = `b"<ws>/<doc>/original"`.
- `settings.BRIEVIE_OCR_TIMEOUT_SECONDS: int` = `env.int(..., default=60)`.
- `ABANDONED_AFTER = timedelta(minutes=45)` (spec risk: 30 pages × 60 s; comment says so).

- [ ] Tests first: field defaults (an app scan has `original_kind == ""`, `original_size is None`); `original_aad(ws, doc) != page_aad(ws, doc, 1)`; `test_abandoned_after_exceeds_worst_ocr`: `ABANDONED_AFTER > timedelta(seconds=settings.BRIEVIE_MAX_PAGES * settings.BRIEVIE_OCR_TIMEOUT_SECONDS)`; in `test_deploy_files.py`: the Dockerfile's last stage installs `tesseract-ocr-nld`.
- [ ] Run → FAIL. Implement; `uv add pypdfium2 Pillow` (pin as the lock gives); three DECISIONS lines (licence, why, rejected alternatives from spec 1.1, the Task 0 result table in one sentence); `makemigrations`, `makemigrations --check` clean.
- [ ] Run the new file and `test_deploy_files.py` (database tests in CI); `make lint typecheck`; commit `feat(intake): original fields, OCR settings and the new dependencies`.

### Task 2: File checks (pure)

**Files:**
- Create: `backend/apps/intake/files.py`, `backend/tests/intake_fixtures.py`
- Test: `backend/tests/test_intake_files.py` (no database)

**Interfaces — Produces (`apps.intake.files`):**
- `class FileKind(StrEnum)`: `PDF = "pdf"`, `JPEG = "jpeg"`, `PNG = "png"` (values equal `Document.OriginalKind`).
- `class FileRefused(Exception)` with `code: str`, one of `file_type_not_supported`, `file_too_large`, `too_many_pages`, `file_encrypted`, `file_unreadable`, `image_too_large`.
- `@dataclass(frozen=True) class CheckedFile: kind: FileKind; page_count: int; size: int; sha256: str`.
- `sniff(data: bytes) -> FileKind` — `%PDF-`, `FF D8 FF`, the 8-byte PNG signature; else `file_type_not_supported`.
- `check_file(data: bytes, *, max_bytes: int, max_pages: int) -> CheckedFile` — size first, then kind, then page count / pixels (images: `jpeg_size` from `apps.documents.jpeg`, PNG from IHDR; > `MAX_IMAGE_PIXELS` → `image_too_large`). Sets `PIL.Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS` at import.
- `pdf_page_count(data: bytes, *, timeout: float = 5.0) -> int` — opens with `pypdfium2` in a child process (`multiprocessing.get_context("spawn")`), killed after `timeout` → `file_unreadable`; a password error → `file_encrypted`.
- `safe_original_name(name: str) -> str` — last component after `/` or `\`, control characters removed, `mask_bsns`, ≤ 200 characters.
- Fixtures (`tests/intake_fixtures.py`): `make_pdf(pages: list[str | None]) -> bytes` (hand-written PDF; a `str` page has a Helvetica text layer, `None` an image-only page with an embedded JPEG), `make_encrypted_pdf() -> bytes` (standard security handler revision 3, user password `geheim`, RC4/MD5 written with `hashlib` in the helper), `make_jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes`, `make_png(width: int, height: int) -> bytes`, `png_header_only(width: int, height: int) -> bytes`.

- [ ] Tests first: each kind sniffed from content with a wrong-looking name irrelevant; `b"GIF89a..."` → `file_type_not_supported`; `test_empty_file_refused` (`b""` → `file_type_not_supported`); `max_bytes + 1` → `file_too_large`; 31-page PDF with `max_pages=30` → `too_many_pages`; encrypted → `file_encrypted`; truncated PDF → `file_unreadable`; `png_header_only(10_000, 5_000)` → `image_too_large`; `CheckedFile.sha256` equals `hashlib.sha256(data).hexdigest()`; `test_original_name_base_only` (`safe_original_name("C:\\fakepath\\brief.pdf") == "brief.pdf"`, a name holding an elfproef-valid number has `[BSN]`, 300 characters → 200).
- [ ] Run → FAIL; implement; run → PASS (locally: no database); lint, typecheck; commit `feat(intake): check a file before anything is stored`.

### Task 3: Render pages and OCR

**Files:**
- Create: `backend/apps/intake/ocr.py`, `backend/apps/intake/render.py`
- Modify: `backend/pyproject.toml` (pytest marker `tesseract`), `.github/workflows/ci.yml` (`backend` job: apt `tesseract-ocr tesseract-ocr-nld`)
- Test: `backend/tests/test_intake_render.py` (no database)

**Interfaces — Consumes:** Task 2 `FileKind`, fixtures. **Produces:**
- `class OcrEngine(Protocol): def read(self, jpeg: bytes) -> str: ...`; `class OcrFailed(Exception)`.
- `class TesseractOcr` (`__init__(self, *, timeout: float)`): `["tesseract", "stdin", "stdout", "-l", "nld+eng"]`, input on stdin, no shell, env `OMP_THREAD_LIMIT=1`; missing binary, timeout or non-zero exit → `OcrFailed`.
- `ocr_engine() -> OcrEngine` — `TesseractOcr(timeout=settings.BRIEVIE_OCR_TIMEOUT_SECONDS)`.
- `class FakeOcr` in `tests/intake_fixtures.py`: returns fixed text, records calls, can raise `OcrFailed` for chosen call numbers.
- `@dataclass(frozen=True) class RenderedPage: jpeg: bytes; text: str` (text unmasked; the caller masks).
- `render_pages(data: bytes, kind: FileKind, ocr: OcrEngine) -> list[RenderedPage]` — raises `apps.pipeline.errors.PermanentError` with `file_unreadable`, `file_encrypted`, `too_many_pages` (> `BRIEVIE_MAX_PAGES`), or `ocr_failed` when OCR was needed and failed on every such page. A page whose OCR failed while others succeeded gets text `""`.

- [ ] Tests first: a text-layer page uses its text and `FakeOcr` is not called; an image-only page calls OCR once; mixed PDF → `[layer text, ocr text]`; every page JPEG (`jpeg_size` not None) with longest side ≤ 2500; `test_jpeg_exif_orientation_applied` (`make_jpeg(400, 200, orientation=6)` → output width < height); PNG → JPEG; encrypted, damaged, 31 pages → their codes; OCR failing on both of two image pages → `ocr_failed`; failing on one → that page `""`, no error; `@pytest.mark.tesseract` `test_real_tesseract_reads_dutch` (skipped when `shutil.which("tesseract")` is None): an image of "Betaal vóór 3 december 2026" rendered with Pillow reads back containing `"3 december 2026"`.
- [ ] Run → FAIL; implement; run → PASS (the marked test skips on the Mac; CI runs it); lint, typecheck; commit `feat(intake): render PDF and image pages, read text with Tesseract`.

### Task 4: Store a file as a processing document

**Files:**
- Create: `backend/apps/intake/services.py`
- Test: `backend/tests/test_intake_services.py` (database: CI)

**Interfaces — Consumes:** Tasks 1-2; `defer_processing`, `default_storage`, `DocumentStorage`. **Produces:**
- `create_document_from_file(*, workspace: Workspace, data: bytes, client_id: UUID, source: Document.Source, scanned_by: User | None, original_name: str, storage: DocumentStorage | None = None) -> tuple[Document, bool]` — `check_file` first (raises `FileRefused`, nothing stored); then as `create_document_from_upload`: an existing `(workspace, client_id)` with the same `original_sha256` → `(doc, False)`, another → `ApiError(409, "document_conflict")`; the `IntegrityError` race handled the same way; one transaction writes the row (`status=processing`, `page_count=0`, `ocr_text=""`, original fields, `original_name=safe_original_name(...)`), the sealed original at `original_path` with `original_aad`, and `defer_processing`; on any exception the written file is deleted. (The spec's `kind` parameter is dropped: the kind is sniffed. `preset_addressee` comes in Task 12.)

- [ ] Tests first: the stored file opens with `original_aad` and equals the input, and fails to open with `page_aad(ws, doc, 1)`; fields and status; one `process_document` job queued; repeat → `False` and the same id, one row; other bytes, same `client_id` → 409 `document_conflict`; refused file → no `Document`, storage directory empty; `defer_processing` monkeypatched to raise → no row, no file.
- [ ] Run (CI) → FAIL; implement; run → PASS; lint, typecheck; commit `feat(intake): store an uploaded file sealed and queue it`.

### Task 5: The pipeline's first step: prepare

**Files:**
- Create: `backend/apps/intake/prepare.py`
- Modify: `backend/apps/pipeline/jobs.py`, `backend/apps/documents/intake.py` (extract the page-writing loop of `_store_pages` into a function both use)
- Test: `backend/tests/test_intake_prepare.py` (database: CI)

**Interfaces — Consumes:** Tasks 1-4, `mark_failed`, `run_pipeline`. **Produces:**
- `prepare_document(workspace_id: UUID, document_id: UUID, *, ocr: OcrEngine | None = None, storage: DocumentStorage | None = None) -> None` — no-op unless the document is `processing`, has `original_kind != ""` and `page_count == 0`. Reads and opens the original outside any transaction, SHA-256 must equal `original_sha256` (else `PermanentError("file_unreadable")`), renders, masks each page text; then one short `workspace_context` + `transaction.atomic` under `select_for_update`, re-checks `processing` and `page_count == 0`, writes sealed pages + `DocumentPage` rows, `page_count`, `ocr_text = PAGE_SEPARATOR.join(masked)`; written files removed on failure.
- In `apps.documents.intake`: `write_pages(document: Document, pages: Sequence[tuple[bytes, str, str]], storage: DocumentStorage, written: list[str]) -> None` — `(jpeg, sha256, masked_text)` per page; `_store_pages` calls it.
- In `apps.pipeline.jobs`: `process(workspace_id: UUID, document_id: UUID) -> None` = `prepare_document` then `run_pipeline`; `run_job`'s default `run` becomes `process`.

- [ ] Tests first (fake AI provider as the existing pipeline tests use; `FakeOcr`): an uploaded two-page PDF ends `needs_review` with two pages that open with their page AAD, `ocr_text` holding both texts; a page text with an elfproef-valid number is stored with `[BSN]` in page and document text; consent off (mode `off`) → no provider call and still pages; with AI on, the provider request never contains `original_name`; autopilot on → the same result as for an app scan with the same text; tampered original (`original_sha256` changed) → `failed` / `file_unreadable`; encrypted → `failed` / `file_encrypted`; `test_retry_after_prepare_keeps_pages` (provider raises `RetryableError` once: after the retry the document has exactly 2 `DocumentPage` rows and `FakeOcr` was called once per page in total); an app scan (no original) is untouched by `prepare_document`.
- [ ] Run (CI) → FAIL; implement; run the new file plus `tests/test_pipeline_*.py`; lint, typecheck; commit `feat(intake): render uploaded files before the pipeline runs`.

### Task 6: API: upload, original download, document fields

**Files:**
- Create: `backend/apps/intake/api.py`, `backend/apps/intake/schemas.py`
- Modify: `backend/brievie/api.py` (add the router), `backend/apps/documents/schemas.py`, `backend/apps/common/errors.py` (`_DOCUMENT_UPLOAD_PATH` also matches `/documents/files`), `deploy/Caddyfile` (`@documentUpload` `path_regexp ^/api/v1/workspaces/[^/]+/documents(/files)?$`), `docs/api/openapi.json` (`make openapi`), `web/src/api/schema.d.ts` (`pnpm gen:api`)
- Test: `backend/tests/test_intake_api.py`; extend `test_deploy_caddyfile.py`, `test_openapi_contract.py` runs unchanged

**Interfaces — Consumes:** Tasks 4-5. **Produces:**
- `POST /workspaces/{workspace_id}/documents/files`, `operation_id="uploadDocumentFile"`, router default auth (session with CSRF or device token, as `createDocument`), `workspace_member(..., min_role=MEMBER)`; multipart `file: UploadedFile = File(...)`, `client_id: UUID = Form(...)`. 201/200 `DocumentOut`, 409 `document_conflict`, 413 `file_too_large`, 422 per `FileRefused.code` and `validation_error`. Messages: the spec's section 2 sentences; `image_too_large`: "Deze afbeelding is te groot. Maak een kleinere foto of scan."
- `GET /workspaces/{workspace_id}/documents/{document_id}/original`, `operation_id="downloadDocumentOriginal"`, `min_role=VIEWER`: the opened original with the kind's content type, `Content-Disposition: attachment` with the stored name (RFC 5987), `Cache-Control: no-store`; 404 `original_not_found` when `original_kind == ""`.
- `DocumentSummaryOut` (so also `DocumentOut`): `source: str = "scan"`, `has_original: bool = False`, `original_name: str = ""`.

- [ ] Tests first: viewer 403; member 201 with `source == "upload"`, `has_original`, `scanned_by` the uploader; device token accepted; session without CSRF refused (403 `csrf_failed`); same file again 200; conflict 409; GIF 422 `file_type_not_supported` with the Dutch message; encrypted 422 `file_encrypted`; a refused file leaves no `Document` and no file; download returns the exact bytes with `no-store`; download for an app scan 404; JSON body to the upload path refused like `createDocument`; Caddyfile matches both paths with the upload limit.
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`; run the new file, `test_documents_upload_api.py`, `test_openapi_contract.py`, `test_deploy_caddyfile.py`; lint, typecheck; commit `feat(intake): upload a PDF or photo and download the original`.

### Task 7: Web: "Brief toevoegen"

**Files:**
- Create: `web/src/components/upload-documents.tsx` (+ `.test.tsx`)
- Modify: `web/src/routes/post.tsx` (+ test), `web/src/routes/document.tsx` (+ test), `web/src/api/queries.ts`, `web/src/locales/*` (`pnpm extract`)

**Interfaces — Consumes:** `uploadDocumentFile`, `downloadDocumentOriginal`, `source`, `has_original`. **Produces:** `<UploadDocuments workspaceId={string} />`: button "Brief toevoegen", a drop zone covering the post page ("Sleep PDF's of foto's hierheen"), `<input type="file" multiple accept="application/pdf,image/jpeg,image/png">`, one request per file with its own `crypto.randomUUID()` as `client_id` (kept on retry), at most 3 at a time, a progress line per file, refuses more than 50 ("Kies maximaal 50 bestanden tegelijk."), the sentence "Eén bestand wordt één brief. …" from spec 1.3. Hidden for viewers. Refreshes the document list after each success.

- [ ] Tests first (by role, mock server as existing route tests): a viewer sees no button; choosing 2 files sends 2 requests and shows "Toegevoegd. Brievie leest de brief nu." twice; 51 files → the refusal and no request; a 422 `file_encrypted` shows its server message on that file's line and the other file still succeeds; at most 3 requests in flight with 5 files; retrying a failed file reuses its `client_id`; `document.tsx` shows "Origineel downloaden" only when `has_original`, and "Binnengekomen via upload" / "via de scanmap" from `source`.
- [ ] Run `cd web && pnpm test` → FAIL; implement; `pnpm extract`; `pnpm test lint typecheck build` → PASS; commit `feat(web): add letters by uploading PDFs and photos`.

### Task 8: Image proof and documents of record

**Files:**
- Modify: `.github/workflows/ci.yml` (`stack` job), `docs/SELFHOST.md`, `docs/PRIVACY.md`, `docs/GLOSSARY.md`, `docs/SPEC.md` (section 10: Tesseract instead of OCRmyPDF, pointing to DECISIONS)

- [ ] Add to the `stack` job after the stack is up: `$COMPOSE exec -T worker tesseract --list-langs` contains the line `nld`; `$COMPOSE exec -T worker python -c "import pypdfium2, PIL"` succeeds. Expected in CI: both pass.
- [ ] Docs: PRIVACY (the original is kept sealed next to the pages; upload storage roughly doubles; OCR happens on the server, nothing leaves it unless AI consent is recorded), SELFHOST (image size + about 60-80 MB; OCR time on a small NAS), GLOSSARY (Brief toevoegen, Origineel, Scanmap = scan folder).
- [ ] Commit `docs(intake): web upload in the self-host and privacy notes`. **Phase 1 ends here: mergeable.**

---

## Phase 2: the scan folder

### Task 9: Scan-folder models, settings and the `intake` service

**Files:**
- Modify: `backend/apps/documents/models.py` (`DocumentEvent.Origin.WATCH_FOLDER = "watch_folder"`), `backend/apps/documents/organise.py` (`AUTOMATIC_ORIGINS` gains it), `backend/brievie/settings.py`, `deploy/docker-compose.yml`, `deploy/.env.example`, `backend/tests/test_deploy_files.py`
- Create: `backend/apps/intake/models.py`, migrations (`documents`, `intake`, RLS forced)
- Test: `backend/tests/test_scan_models.py`

**Interfaces — Produces:**
- `ScanFolderSettings(WorkspaceModel)`: `enabled=False`, `folder_name: CharField(60, blank)`, `per_person=False`, `check_state: CharField(20, default="unknown")`, `owner_uid: IntegerField(default=0)`, `owner_gid: IntegerField(default=0)`, `checked_at: DateTimeField(null)`; unique `workspace`; unique `folder_name` where `folder_name != ""` (enforced across workspaces by the index even under RLS: a taken name shows only as `IntegrityError`).
- `ScanFolderSettings.unknown_folders: JSONField(default=list)` — the job's last view of unknown subfolders, `[{"name": <masked>, "files": <int>}]`, at most 50 entries, written each round.
- `ScanFolder(WorkspaceModel)`: `person: FK(Person, null, on_delete=SET_NULL)` (a `Person` of kind `person` or `department`), `name: CharField(60)`, `added_by_admin: bool=False`, `state: CharField(10, default="active")` (`active`, `paused` (a department while departments are off), `stopped` (an admin stopped it), `gone` (person archived, deleted, or no longer linked to an account and not added by hand)); unique `(workspace, person)` where person is not null, unique `(workspace, Lower("name"))`.
- `ScanFile(WorkspaceModel)`: `relative_path: CharField(400)`, `size`, `mtime_ns`, `device`, `inode` (`BigIntegerField`), `stable_since: DateTimeField`, `state` (`seen`, `imported`, `failed`), `reason: CharField(40, blank)`, `document: FK(Document, null, SET_NULL)`, `removed_at: DateTimeField(null)`; unique `(workspace, relative_path, device, inode)`.
- Settings: `BRIEVIE_SCAN_ROOT` (`env.str`, default `/scan`), `BRIEVIE_SCAN_DIR_SET` (`env.bool`, default False), `BRIEVIE_SCAN_STABLE_SECONDS` (60).
- Compose service `intake`: same image as `worker`, command `python manage.py check_master_key && exec python manage.py procrastinate worker --queues intake`, volumes `documents` and `${BRIEVIE_SCAN_DIR:-scan-unset}:/scan`, `group_add: ["${BRIEVIE_SCAN_GID:-100}"]`, env `BRIEVIE_SCAN_DIR_SET: ${BRIEVIE_SCAN_DIR:+true}` plus what `worker` gets, no `ports`; named volume `scan-unset`; commented `BRIEVIE_SCAN_DIR` / `BRIEVIE_SCAN_GID` lines in `.env.example`.

- [ ] Tests first: defaults; two folders `Anne` and `anne` in one workspace → `IntegrityError`; RLS forced on the three tables (as existing RLS tests check); two workspaces with the same non-empty `folder_name` → `IntegrityError`, two empty names allowed; `WATCH_FOLDER in AUTOMATIC_ORIGINS`; deploy file tests: only `intake` mounts `/scan`, `worker` and `api` do not; `intake` has no `ports` and `group_add` defaults to 100.
- [ ] Run (CI) → FAIL; implement; `makemigrations --check` clean; run; lint, typecheck; commit `feat(scan): scan-folder tables and the intake service`.

### Task 10: Guarded file functions

**Files:**
- Create: `backend/apps/intake/scan_files.py`
- Test: `backend/tests/test_scan_files.py` (no database; `tmp_path` only)

**Interfaces — Produces (`apps.intake.scan_files`, the only code that touches the share):**
- `class ScanRefused(Exception)` with `code: str`.
- `RelPath = tuple[str, ...]`; `@dataclass(frozen=True) class Fingerprint: device: int; inode: int; size: int; mtime_ns: int`.
- `@dataclass(frozen=True) class ImportedFile: path: RelPath; fingerprint: Fingerprint` with `@classmethod from_row(cls, row: ScanFile) -> ImportedFile` — refuses (`not_imported`) unless `row.state == "imported"` and `row.document_id` is set; reads no database.
- `open_root() -> int` — `realpath(settings.BRIEVIE_SCAN_ROOT)`; refuses `root_unset` (`BRIEVIE_SCAN_DIR_SET` false), `root_missing`, `root_is_slash`, `root_overlaps_storage` (equal to, inside, or containing `BRIEVIE_STORAGE_ROOT`'s realpath).
- `ensure_folder(root_fd: int, path: RelPath) -> None` — `mkdirat` one level at a time, existing is fine.
- `list_candidates(root_fd: int, folder: RelPath) -> list[tuple[RelPath, Fingerprint]]` — regular files only, candidate names per Task 11 `is_candidate_name`, deeper directories ignored.
- `read_candidate(root_fd: int, path: RelPath, expected: Fingerprint, *, max_bytes: int) -> bytes` — `changed` when `fstat` differs.
- `remove_imported(root_fd: int, imported: ImportedFile) -> None` — `unlink(name, dir_fd=parent)` only when the current `fstat` equals the fingerprint; else `changed`.
- `move_to_failed(root_fd: int, path: RelPath, *, reason_text: str) -> RelPath` — `rename` into `<workspace folder>/mislukt/` (`src_dir_fd`/`dst_dir_fd`), a taken name gets ` (2)`, ` (3)`…; never overwrites another file: the target name is first reserved by creating an empty placeholder with `O_CREAT|O_EXCL|O_NOFOLLOW`, then `os.rename` replaces only that placeholder; writes `<name>.reden.txt` with `O_CREAT|O_EXCL|O_NOFOLLOW`. Refuses a source already under `mislukt/` (`failed_untouchable`).
- `list_failed(root_fd: int, folder: RelPath) -> set[str]` — names in `<folder>/mislukt/` (read only; for the status list).
- `probe(root_fd: int, folder: RelPath) -> ProbeResult` — `@dataclass(frozen=True) ProbeResult: state: str; owner_uid: int; owner_gid: int`, state `ok` / `missing` / `not_readable` / `not_writable`. Uses the fixed name `.brievie-check` (`O_CREAT|O_EXCL|O_NOFOLLOW`) and unlinks it only when its inode equals the one it just created. This is the only function that writes or removes a dot name (the spec's path rule refuses dot names, so the probe is not routed through `remove_imported`).
- Shared rules, each checked inside every function: components non-empty, not `.`/`..`, no `/` or NUL, no leading `.`; at most 2 components below the workspace folder; every directory opened with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW` and `dir_fd`; files with `O_NOFOLLOW`; `S_ISREG` and `st_nlink == 1`. No `rmtree`, no glob, no directory removal anywhere in the module (a test greps the source for `rmtree`, `rmdir`, `glob`, `removedirs`).

- [ ] Tests first, with an autouse fixture that sets `BRIEVIE_SCAN_ROOT` to a fresh `tmp_path / "scan"`, `BRIEVIE_STORAGE_ROOT` to `tmp_path / "storage"`, and fails the test if either resolves outside `tmp_path_factory.getbasetemp()`: each `open_root` refusal (`/`, equal to storage, inside storage, containing storage, unset); `..`, absolute and NUL components refused; a symlinked file, a symlinked workspace folder and a symlinked person folder are refused and their target outside the root still exists; a hard-linked file refused; a FIFO is not a candidate and `read_candidate` on it refuses; a file changed after import (size or mtime) is not removed; `remove_imported` with a `seen` row refused by `from_row`; `move_to_failed` keeps an existing `mislukt/brief.pdf` and makes `brief (2).pdf`; a file in `mislukt/` refused; `probe` leaves no `.brievie-check` behind and reports `not_writable` on a `0o555` folder; the source grep.
- [ ] Run locally (`uv run pytest tests/test_scan_files.py`, no database) → FAIL; implement; run → PASS; lint, typecheck; commit `feat(scan): guarded functions for the scan folder`.

### Task 11: Scan-folder rules (pure)

**Files:**
- Create: `backend/apps/intake/scan_rules.py`
- Test: `backend/tests/test_scan_rules.py` (no database)

**Interfaces — Produces:**
- `RESERVED = "mislukt"`; `is_reserved(name: str) -> bool` (`mislukt`, leading `.`, `@`, `#`, `~`).
- `is_candidate_name(name: str) -> bool` — `.pdf/.jpg/.jpeg/.png` any case, not hidden, not `.tmp`/`.part`.
- `stable(previous: Fingerprint | None, stable_since: datetime | None, current: Fingerprint, now: datetime, *, stable_seconds: int) -> tuple[bool, datetime]` — returns (take it, new `stable_since`); a changed size/mtime restarts the clock; size 0 never.
- `valid_folder_name(name: str) -> bool` — `^[a-z0-9-]{1,60}$` (lower case only: SMB is case-insensitive, so names cannot differ by case), not reserved; `default_folder_name(workspace_name: str) -> str`.
- `person_folder_names(names: Mapping[UUID, str], *, taken: Mapping[UUID, str]) -> dict[UUID, str]` — `names` holds persons **and departments** together (one namespace on disk); strips `\ / : * ? " < > |` and control characters, trims, ≤ 60 characters, not reserved, keeps the existing name of an id in `taken`, adds ` (2)`… comparing `casefold()` against every name in `taken` and the result, in id order.
- `folder_targets(persons: Sequence[FolderCandidate], *, departments_enabled: bool) -> FolderPlan` — pure: `FolderCandidate(id, kind, display_name, archived, has_account, added_by_admin, state)`; returns which ids should be `active`, `paused` or `gone` (spec 3.2a: persons with an account or added by hand are active; departments active while departments are on, else paused; archived or no longer qualifying → gone; `stopped` is kept).
- `classify_subfolders(names_on_disk: Collection[str], known: Mapping[str, str]) -> tuple[dict[str, str], list[str]]` — `known` maps lower-case folder names to their state; returns (state per on-disk name, unknown names), skipping `is_reserved` names; the comparison is case-insensitive.
- `scan_client_id(workspace_id: UUID, path: RelPath, sha256: str, mtime_ns: int) -> UUID` — `uuid5` with a fixed namespace constant `SCAN_NAMESPACE`.

- [ ] Tests first: `stable` false at the first look, false after 59 s, true after 60 s unchanged, false again after a size change; `is_candidate_name("Scan.PDF")` true, `"scan.pdf.part"` false, `"._scan.pdf"` false; `default_folder_name("Kantoor Den Haag")  == "kantoor-den-haag"`; `test_folder_names_unique_ignoring_case` (`{a: "Anne", b: "anne"}` → `{"Anne", "anne (2)"}` in id order); a renamed person keeps the taken name; `"Mislukt"` as a person name does not become `mislukt`; `test_department_and_person_same_name` (person "Financien" and department "Financien" → `Financien`, `Financien (2)`); `folder_targets`: a person without an account is not active unless `added_by_admin`, a department is `paused` with departments off and `active` with them on, an archived person is `gone`, `stopped` stays `stopped`; `classify_subfolders`: `Anne` on disk matches a known `anne`, `@eaDir` and `mislukt` skipped, `Oude scans` unknown; `scan_client_id` stable for equal input, different for another mtime.
- [ ] Run locally → FAIL; implement; run → PASS; lint, typecheck; commit `feat(scan): pure rules for names, stability and ids`.

### Task 12: The scan job

**Files:**
- Create: `backend/apps/intake/scan_folder.py`, `backend/apps/intake/jobs.py`
- Modify: `backend/apps/intake/services.py` (`preset_addressee`)
- Test: `backend/tests/test_scan_folder_job.py` (database: CI; share in `tmp_path`)

**Interfaces — Consumes:** Tasks 4, 9-11, `organise.add_addressee`. **Produces:**
- `create_document_from_file(..., preset_addressee: Person | None = None)` — inside its transaction, `with visibility.addressee_scope(document): organise.add_addressee(document, person, actor=None, origin=DocumentEvent.Origin.WATCH_FOLDER)`, before `defer_processing`. The person is re-read under the workspace context in that transaction; archived or gone by then → the file is moved with `person_gone` instead.
- `REASON_TEXTS: Mapping[str, str]` — a Dutch B1 sentence per refusal code plus `person_gone` (e.g. "Brievie kon dit bestand niet lezen: de PDF is beveiligd met een wachtwoord.").
- `scan_workspace(workspace_id: UUID, root_fd: int, *, now: datetime, limit: int = 20) -> None` — ensure folders (workspace, `mislukt`; when `per_person`: `folder_targets` over the workspace's persons and departments (one query, as the system inside the workspace context) and `Workspace.departments_enabled`, new `ScanFolder` rows named by `person_folder_names`, `ensure_folder` for `active` ones only; an existing directory with that name is adopted); list the workspace folder's subdirectories and `classify_subfolders`: `active` folders are read, `paused` folders are counted but not read, `gone` folders have their files moved with `person_gone`, `stopped` and unknown folders are not read and nothing in them is moved or removed (their names, `mask_bsns`, and file counts go into `ScanFolderSettings.unknown_folders`; stopped ones are listed there too); a file in an `active` folder is imported with `preset_addressee` = that folder's person or department; write the `probe` result into `ScanFolderSettings`; list; record sightings in `ScanFile`; for each stable file (≤ `limit`): read → in one transaction `create_document_from_file(source=WATCH_FOLDER, scanned_by=None, original_name=<file name>, client_id=scan_client_id(...))` **and** the row set to `imported` with the document and fingerprint → after commit read the sealed original back and compare SHA-256 → `remove_imported(ImportedFile.from_row(row))` → `removed_at`. `FileRefused` / `PermanentError` → `move_to_failed`, row `failed` with the code and its `relative_path` set to the new `mislukt/` path (so a file put back keeps its inode but meets no `failed` row at its old path); `failed` rows whose name `list_failed` no longer returns are deleted. `DatabaseError`, `OSError` other than a refusal (full disk): file left, next round.
- `scan_round(*, now: Callable[[], datetime] = timezone.now) -> None` — `open_root()` (a refusal logs its code once per process and returns), each workspace with `enabled` in its own `workspace_context` (iterate workspaces as `requeue_abandoned_documents` does), purge `imported` rows with `removed_at` older than 30 days.
- `apps.intake.jobs`: `@app.periodic(cron="*/1 * * * *")` + `@app.task(queue="intake", name="scan_folders")` `scan_folders(timestamp: int) -> None`.

The spec orders "remove, then mark imported" (3.3) but lets `remove_imported` act only on an `imported` row (3.5); this plan marks the row in the import transaction and removes after the read-back, which satisfies both and keeps the crash window idempotent.

- [ ] Tests first (fake clock; `FakeOcr` not needed: the job only stores, the pipeline is queued): an unstable file waits (no document after the first round); stable after 60 s → one `processing` document with `source=watch_folder`, `scanned_by=None`, file gone, row `imported`; crash between commit and removal (monkeypatch `remove_imported` to raise once) → next round answers with the same document, no second one, file removed; an encrypted PDF → moved to `mislukt/` with `.reden.txt` holding the Dutch sentence, row `failed`; files already in `mislukt/` are never listed, moved or removed; a file in `Anne/` → the document has Anne as addressee, `automatic=True`, a history event with origin `watch_folder` and no actor; a deleted person's folder → its file moved with `person_gone`; a department folder → the department is the addressee; `test_department_folder_paused_while_departments_off` (file waits, no document, listed; departments on → imported next round); `test_unknown_subfolder_is_left_alone` (`Oude scans/brief.pdf` stays byte for byte with its mtime, no `ScanFile` row, listed in `unknown_folders` with count 1; no person created); a hand-made folder named exactly like a member's person is adopted and its file gets the addressee; a person without an account gets no folder until `added_by_admin`; `test_person_folder_letter_seen_by_that_person_only` (workspace with "Iedereen ziet alle post" off and "Beheerders lezen alle post" off: Anne's member sees the letter through `visible_documents`, another member does not, `addressee_ids == [anne]`, no `DocumentReader` rows); with `care_is_private` on and a Zorg label applied by the pipeline, the letter becomes private with Anne as reader; the history shows origin `watch_folder` with no actor and the link can be undone; 25 stable files → 20 this round, 5 next; a workspace with the folder off is not read; `test_file_put_back_from_failed_is_tried_again` (an image whose first read failed is moved to `mislukt/`, then renamed back to its old path with the same inode and fixed content; two rounds later it is a document); a file removed by hand from `mislukt/` drops out of `failed`; database error during import → file still there; no file name appears in captured logs.
- [ ] Run (CI) → FAIL; implement; run; lint, typecheck; commit `feat(scan): pick up letters from the scan folder`.

### Task 13: Scan-folder settings: service, audit, API

**Files:**
- Create: `backend/apps/intake/scan_settings.py`
- Modify: `backend/apps/intake/api.py`, `backend/apps/intake/schemas.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`
- Test: `backend/tests/test_scan_settings.py`, `backend/tests/test_scan_settings_api.py`

**Interfaces — Produces:**
- `class ScanFolderError(Exception)` with `reason` in `taken`, `unavailable`, `invalid_name`.
- `scan_folder_status(workspace_id: UUID, *, now: datetime) -> ScanFolderOut` — `available = settings.BRIEVIE_SCAN_DIR_SET`; `check` = stored `check_state`, `unknown` when `checked_at` is older than 5 minutes (the `intake` service is not running); `waiting_count`, `oldest_waiting_minutes` from `seen` rows; `failed` from `failed` rows still unremoved.
- `configure_scan_folder(*, workspace_id: UUID, enabled: bool, folder_name: str, per_person: bool, actor: Actor) -> ScanFolderOut` (`Actor` as `apps.pipeline.settings_service.configure` takes it) — `unavailable` when the server has no folder and `enabled`; `invalid_name` per `valid_folder_name`; `taken` on the unique index (`IntegrityError` in a savepoint); one audit row per changed switch: `record(AuditCode.WORKSPACE_SETTING_CHANGED, values={"setting": "scan_folder" | "scan_folder_per_person", "value": <bool>})`; a name change alone writes no row and the name is never in a row.
- `ScanFolderOut`: `available: bool = False`, `enabled: bool = False`, `folder_name: str = ""`, `per_person: bool = False`, `check: str = "unknown"`, `owner_uid: int = 0`, `owner_gid: int = 0`, `waiting_count: int = 0`, `oldest_waiting_minutes: int = 0`, `failed: list[ScanFailureOut] = []`; `ScanFailureOut`: `name: str`, `reason: str`, `failed_at: datetime`. `ScanFolderIn`: `enabled: bool`, `folder_name: str = ""`, `per_person: bool = False`.
- `GET` / `PUT /workspaces/{workspace_id}/scan-folder`, `operation_id` `getScanFolder` / `updateScanFolder`, both `auth=session_auth`, admin; 409 `scan_folder_taken`, 409 `scan_folder_unavailable`, 422 `validation_error` for a bad name.
- `ScanFolderOut` also: `folders: list[ScanSubfolderOut] = []` (`id: UUID`, `name: str`, `person_id: UUID`, `person_name: str`, `kind: str` (`person` | `department`), `state: str`, `waiting_count: int = 0`), `unknown_folders: list[ScanUnknownFolderOut] = []` (`name: str`, `files: int = 0`), `departments_enabled: bool = False`.
- `add_scan_folder(*, workspace_id: UUID, person_id: UUID, name: str, actor: Actor) -> ScanFolderOut` and `POST /workspaces/{workspace_id}/scan-folder/folders` `addScanFolder` (session, admin): body `person_id: UUID`, `name: str = ""` (empty → `person_folder_names`; given → must pass the name rules, may be an unknown folder's name, which links it). Sets `added_by_admin`, `state=active` (a `stopped` row is made active again). 404 `person_not_found`; 409 `scan_subfolder_taken` (name used by another id, ignoring case); 409 `scan_folder_departments_off` for a department while departments are off. Audit `workspace_setting_changed` `setting=scan_folder_person`, `value=added`.
- `remove_scan_folder(...)` and `DELETE /workspaces/{workspace_id}/scan-folder/folders/{folder_id}` `removeScanFolder`: `state=stopped`; no file or directory touched. Audit `value=removed`. No person name and no folder name in any audit row.

- [ ] Tests first: member 403; device token 401; server without folder → `available` false and PUT enabled → 409 `scan_folder_unavailable`; a name taken by another workspace → 409 `scan_folder_taken`; `"Kantoor"` → 422; on → one audit row naming the admin with `setting=scan_folder`, `value=True`; nothing changed → no row; the folder name appears in no audit row; `addScanFolder` for a person without an account → `added_by_admin` and active; with an unknown folder's name → linked, and the next status no longer lists it as unknown; a name taken by a department → 409 `scan_subfolder_taken`; `removeScanFolder` → `stopped`, the directory still exists (share in `tmp_path`); member 403 and device token 401 on both; `check` is `unknown` after 6 minutes without a probe; `waiting_count` and `failed` reflect rows.
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`, contract test; lint, typecheck; commit `feat(scan): scan-folder settings for workspace admins`.

### Task 14: Web: "Scanmap" page

**Files:**
- Create: `web/src/routes/scan-folder.tsx` (+ `.test.tsx`)
- Modify: `web/src/router.tsx`, `web/src/routes/root.tsx` (settings link next to the `ai` page, shown only to admins when `available`), `web/src/api/queries.ts`, locales

**Interfaces — Consumes:** Task 13 endpoints.

- [ ] Tests first: not shown to a member or when `available` is false; switching on with the default name sends `updateScanFolder`; 409 `scan_folder_taken` shows the server message; `check: "not_writable"` shows "Brievie kan de scanmap niet lezen. Geef de groep 'users' lees- en schrijfrechten op deze map."; `ok` shows "De scanmap werkt."; `oldest_waiting_minutes > 60` shows "Er wachten 4 bestanden langer dan een uur. Draait Brievie?" with the count; the failed list shows name, Dutch reason and date and offers no remove or move action; the switch reads "Mappen per persoon en afdeling"; the folder list shows folder name, current person or department name and state ("Wacht: afdelingen staan uit" for `paused`); "Map maken" per person without a folder sends `addScanFolder`; an unknown folder shows "Brievie leest de map 'Oude scans' niet. Koppel hem aan een persoon of afdeling, of haal de bestanden eruit." with "Koppel aan …" (a person/department picker) sending `addScanFolder` with that name; "Niet meer lezen" sends `removeScanFolder` after a confirm that says the folder and its files stay on the NAS; the page explains that the letter lands with that person and who else sees it follows the workspace's "Wie ziet wat"; the set-up text includes "Zet je scanner op één bestand per brief." and that a removed file is in Brievie, not in the recycle bin.
- [ ] Run → FAIL; implement; `pnpm extract`; `pnpm test lint typecheck build`; commit `feat(web): the Scanmap settings page`.

### Task 15: Container proof, docs and the owner's check

**Files:**
- Create: `.github/scripts/scan_folder_setup.py`
- Modify: `.github/workflows/ci.yml` (new job `scan-folder-proof`), `docs/SELFHOST.md`, `docs/PRIVACY.md`, `docs/DECISIONS.md`

- [ ] CI job `scan-folder-proof` (runs only in the runner): `sh deploy/init-env.sh`; `mkdir -p $RUNNER_TEMP/scan/kantoor $RUNNER_TEMP/outside`; `sudo chown -R 20000:100 $RUNNER_TEMP/scan && sudo chmod -R 2770 $RUNNER_TEMP/scan` (owner uid differs from 10001, group 100); a PDF `outside/keep.pdf`, a symlink `scan/kantoor/link.pdf -> $RUNNER_TEMP/outside/keep.pdf`, a symlinked directory `scan/kantoor/dir -> $RUNNER_TEMP/outside`; `BRIEVIE_SCAN_DIR=$RUNNER_TEMP/scan` in `deploy/.env`; stack up; `$COMPOSE exec -T admin python manage.py shell < .github/scripts/scan_folder_setup.py` (creates a workspace with the scan folder `kantoor` on); drop a real PDF `scan/kantoor/brief.pdf` (generated in the runner with Python, no real content); poll up to 4 minutes. Expected: a document exists for it, `brief.pdf` is gone, `outside/keep.pdf` and both symlinks still exist, nothing else under `$RUNNER_TEMP/outside` changed (checksum list before/after), and `getScanFolder`'s stored `check_state` is `ok`.
- [ ] Docs: SELFHOST (the Synology steps of spec 3.7, Linux `chgrp`/`chmod g+rwxs`, SMB1 advice, one share for all workspaces means one trust level), PRIVACY (spec 3.8: unencrypted while waiting, `mislukt/` indefinitely, rights advice, SMB encryption), DECISIONS (remove after verified import, no `verwerkt/`; separate `intake` service; `scan` naming instead of `inbox`; the ruling of spec 3.2a: subfolders per person with an account and per department while departments are on, others by hand, unknown subfolders never read or touched, the folder's person or department is the addressee directly (automatic, `watch_folder`, undoable), not a proposal, and no "privé" folder in v1).
- [ ] Commit `test(scan): prove the guards in a container; document the scan folder`.

**Owner check (manual, not a blocker):** on the owner's Synology, with a test folder and test files only: set `BRIEVIE_SCAN_DIR` and the group `users` read/write per SELFHOST; confirm the "Scanmap" page says "De scanmap werkt."; let the scanner drop one test letter; confirm it arrives and the file disappears from the share. If the page reports `not_writable` or the scanner's files cannot be removed by group 100, report the shown `owner_uid`/`owner_gid` and the ACL screen; the fix is documentation or `BRIEVIE_SCAN_GID`, not code.
