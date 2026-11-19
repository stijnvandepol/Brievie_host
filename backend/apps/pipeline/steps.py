"""The pipeline: a row of small steps from stored text to proposals for review.

Each step has its own input and output and may run again without harm: a job is
retried after a failure, and a document that is no longer `processing` is left alone.
"""

import logging
import time
from collections.abc import Callable, Sequence
from datetime import date
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from apps.common.privacy import mask_bsns
from apps.documents import review, suggestions
from apps.documents.models import Document, DocumentSuggestion, Extraction
from apps.labels.models import Label
from apps.people.matching import PRESELECT_FROM, PersonNames, match_addressees
from apps.people.models import Person
from apps.pipeline.autopilot import apply_open_proposals, decide_confirmation
from apps.pipeline.choices import ExtractionRequest, aliased
from apps.pipeline.errors import PermanentError
from apps.pipeline.providers import LLMProvider
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.settings_service import autopilot_in_force, provider_for
from apps.pipeline.validation import ValidatedChoice, ValidatedValue, validate, validate_choices
from apps.rules import triggers
from apps.workspaces.context import workspace_context

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[UUID], LLMProvider | None]


def run_pipeline(
    workspace_id: UUID,
    document_id: UUID,
    *,
    provider_factory: ProviderFactory = provider_for,
    today: Callable[[], date] = timezone.localdate,
) -> None:
    """Process one document. Safe to run twice: only a `processing` document is touched.

    Every database step has its own short workspace context; no transaction stays
    open while the AI call runs.
    """
    started = time.monotonic()
    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            logger.info("Skipped document %s in workspace %s", document_id, workspace_id)
            return
        text = load_text(document)
        provider = provider_factory(workspace_id)
        persons = active_persons()
        request = None
        if provider is not None:
            request = ExtractionRequest(text, *aliased(_as_choices(persons), _labels()))

    values: list[ValidatedValue] = []
    choices: list[ValidatedChoice] = []
    payload: ExtractionPayload | None = None
    dropped = 0
    day = today()
    if provider is not None and request is not None:
        payload = extract(provider, request)
        values = validate(payload, text, today=day)
        choices, dropped = validate_choices(payload, text, request)

    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            # Confirmed, failed or deleted while the AI was reading: leave it.
            logger.info("Document %s changed during processing; result dropped", document_id)
            return
        store_proposals(document, values, provider)
        _organise_guarded(document, text, persons, choices)
        in_force = autopilot_in_force(workspace_id) if provider is not None else None
        if in_force is not None and in_force.organise:
            _autopilot_guarded(document)
        confirmed = False
        if in_force is not None and in_force.confirm and payload is not None:
            confirmed = _confirm_guarded(
                document, payload, values, text, day, organised=in_force.organise
            )
        if not confirmed:
            document.status = Document.Status.NEEDS_REVIEW
            document.save(update_fields=["status", "updated_at"])
        suggestion_count = _open_suggestion_count(document)
    logger.info(
        "Processed document %s: %d proposals, %d suggestions, %d AI picks dropped, "
        "confirmed by AI: %s, in %.1f s",
        document_id,
        len(values),
        suggestion_count,
        dropped,
        confirmed,
        time.monotonic() - started,
    )


def organise_processed(
    document: Document,
    text: str,
    persons: Sequence[PersonNames],
    ai_choices: Sequence[ValidatedChoice],
) -> None:
    """Propose labels and addressees for a processed document (design 17).

    Runs in the transaction that holds the document's row lock. A run replaces the
    open proposals of an earlier run; accepted and dismissed rows stay. Name matching,
    then the AI's picks, then the rules (trigger `processed`). Rules come last so they
    see the other proposals: a rule's proposal of the same change strengthens that row
    to origin `rule`. Every id is looked up again: a person or label deleted or
    archived since the lists were read is skipped.
    """
    suggestions.clear_open(
        document,
        {
            DocumentSuggestion.Origin.NAME_MATCH,
            DocumentSuggestion.Origin.AI,
            DocumentSuggestion.Origin.RULE,
        },
    )
    matches = match_addressees(text, persons)
    ai_persons = [c.target_id for c in ai_choices if c.kind == "addressee"]
    found_persons = {
        person.id: person
        for person in Person.objects.filter(
            id__in=[m.person_id for m in matches] + ai_persons, archived_at__isnull=True
        )
    }
    found_labels = Label.objects.in_bulk([c.target_id for c in ai_choices if c.kind == "label"])
    for match in matches:
        person = found_persons.get(match.person_id)
        if person is None:
            continue
        suggestions.propose(
            document,
            action=DocumentSuggestion.Action.ADD_ADDRESSEE,
            person=person,
            origin=DocumentSuggestion.Origin.NAME_MATCH,
            quote=match.quote,
            confidence=match.confidence,
            preselected=match.confidence >= PRESELECT_FROM,
        )
    for choice in ai_choices:
        if choice.kind == "addressee":
            person = found_persons.get(choice.target_id)
            if person is None:
                continue
            suggestions.propose(
                document,
                action=DocumentSuggestion.Action.ADD_ADDRESSEE,
                person=person,
                origin=DocumentSuggestion.Origin.AI,
                quote=choice.quote,
                confidence=choice.confidence,
            )
        else:
            label = found_labels.get(choice.target_id)
            if label is None:
                continue
            suggestions.propose(
                document,
                action=DocumentSuggestion.Action.ADD_LABEL,
                label=label,
                origin=DocumentSuggestion.Origin.AI,
                confidence=choice.confidence,
            )
    triggers.on_processed(document)


def active_persons() -> list[PersonNames]:
    """The workspace's non-archived persons with every way their name is written."""
    return [
        PersonNames(person_id, (name, *variants))
        for person_id, name, variants in Person.objects.filter(
            archived_at__isnull=True
        ).values_list("id", "display_name", "name_variants")
    ]


def _as_choices(persons: Sequence[PersonNames]) -> list[tuple[UUID, str, Sequence[str]]]:
    return [(person.person_id, person.names[0], person.names[1:]) for person in persons]


def _labels() -> list[tuple[UUID, str]]:
    return list(Label.objects.values_list("id", "name"))


def _open_suggestion_count(document: Document) -> int:
    return DocumentSuggestion.objects.filter(
        document=document, state=DocumentSuggestion.State.OPEN
    ).count()


def load_text(document: Document) -> str:
    """The document's text for the AI, masked once more: no BSN may leave the server."""
    return mask_bsns(document.ocr_text)


def extract(provider: LLMProvider, request: ExtractionRequest) -> ExtractionPayload:
    """Ask the provider and parse its answer; one more try when the answer is unusable."""
    for _attempt in range(2):
        raw = provider.extract(request)
        try:
            return ExtractionPayload.model_validate_json(_strip_code_fence(raw))
        except ValidationError:
            # Its text repeats the input, so it is neither logged nor passed on.
            continue
    raise PermanentError("ai_invalid_output") from None


def store_proposals(
    document: Document, values: list[ValidatedValue], provider: LLMProvider | None
) -> None:
    """Replace the document's proposals."""
    document.extractions.all().delete()
    for value in values:
        Extraction.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            field=value.field,
            group=value.group,
            value=mask_bsns(value.value),
            quote=mask_bsns(value.quote),
            confidence=value.confidence,
            provider=provider.name if provider is not None else "",
            model=provider.model if provider is not None else "",
        )


def mark_failed(workspace_id: UUID, document_id: UUID, code: str) -> None:
    """Record that processing failed. Only a `processing` document is touched.

    The deterministic proposals are still made (design 17): the user organises the
    document by hand and finds them there.
    """
    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            return
        _organise_guarded(document, load_text(document), active_persons(), [])
        document.status = Document.Status.FAILED
        document.failure_code = code
        document.save(update_fields=["status", "failure_code", "updated_at"])
    logger.warning("Document %s failed: %s", document_id, code)


def _organise_guarded(
    document: Document,
    text: str,
    persons: Sequence[PersonNames],
    ai_choices: Sequence[ValidatedChoice],
) -> None:
    """`organise_processed` in a savepoint: when proposing fails, its writes are rolled
    back and the caller still stores what it has (the AI's proposals, a failure) and
    moves the document on, so it never stays `processing`."""
    try:
        with transaction.atomic():
            organise_processed(document, text, persons, ai_choices)
    except Exception as exc:  # noqa: BLE001 - the document must not stay processing
        # The type only: an exception message may quote the document.
        logger.error(
            "Unexpected %s while proposing for document %s", type(exc).__name__, document.id
        )


def _autopilot_guarded(document: Document) -> None:
    """`apply_open_proposals` in a savepoint: when it fails, its writes are rolled back
    and the document still moves on to review with its proposals open."""
    try:
        with transaction.atomic():
            apply_open_proposals(document)
    except Exception as exc:  # noqa: BLE001 - the document must not stay processing
        # The type only: an exception message may quote the document.
        logger.error(
            "Unexpected %s while applying proposals for document %s",
            type(exc).__name__,
            document.id,
        )


def _confirm_guarded(
    document: Document,
    payload: ExtractionPayload,
    values: Sequence[ValidatedValue],
    text: str,
    today: date,
    *,
    organised: bool,
) -> bool:
    """Confirm the document when every guard rail holds; whether it was confirmed.

    A proposal still open after the organise step also stops it when `organised`: the
    person is then not unambiguous. The confirmation runs in a savepoint: when it fails
    (validation included), its writes are rolled back and the document is reloaded, so
    the caller moves it on to review with its proposals.
    """
    data = decide_confirmation(payload=payload, values=values, text=text, today=today)
    if data is None or (organised and _open_suggestion_count(document) > 0):
        return False
    try:
        with transaction.atomic():
            review.confirm_automatically(document, data)
    except Exception as exc:  # noqa: BLE001 - the document must not stay processing
        # The type only: an exception message may quote the document.
        logger.error("Unexpected %s while confirming document %s", type(exc).__name__, document.id)
        document.refresh_from_db()
        return False
    return True


def _lock_processing(document_id: UUID) -> Document | None:
    """The document, locked, when it exists in this workspace and is still processing."""
    return (
        Document.objects.select_for_update()
        .filter(id=document_id, status=Document.Status.PROCESSING)
        .first()
    )


def _strip_code_fence(raw: str) -> str:
    """Some models wrap their JSON in a Markdown code fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
