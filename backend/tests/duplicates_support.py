"""Letters for the duplicate tests: an annual energy statement, a rescan of it (OCR
character confusions), a reminder of it, and a provider that reads its facts."""

import copy
import json
import uuid
from datetime import date, timedelta
from typing import Final

from django.utils import timezone

from apps.documents.models import Document, DuplicateCandidate
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace

TODAY: Final = date(2026, 3, 20)

LETTER: Final = """Energie Noord BV
Postbus 4410, 9700 AB Groningen
Klantnummer 00451278
Contractnummer 77120934
Factuurnummer F2026-018834
Datum 12 maart 2026

Geachte heer De Vries,

Hierbij ontvangt u de jaarafrekening voor de levering van elektriciteit en aardgas op het
adres Wilhelminastraat 14 te Assen over de periode van 1 maart 2025 tot en met 28 februari
2026.

Meternummer elektriciteit 871687120053312345, meterstand normaaltarief 041235, daltarief
038871. Meternummer gas 871687140009876543, meterstand 012874.

Het totaalbedrag van de jaarafrekening is € 1.234,56. Wij schrijven dit bedrag rond
26 maart 2026 af van uw rekening. Het betalingskenmerk is 1234 5678 9012 3456.

Uw nieuwe termijnbedrag wordt € 145,00 per maand vanaf 1 april 2026.

Heeft u vragen over deze afrekening? Kijk dan op onze website of bel onze klantenservice
op werkdagen tussen 08.00 en 20.00 uur.

Met vriendelijke groet,
Klantenservice Energie Noord
"""

# The same letter a month later: other date and amount, the rest of the template equal.
REMINDER: Final = LETTER.replace("Datum 12 maart 2026", "Datum 9 april 2026").replace(
    "€ 1.234,56", "€ 1.310,20"
)

OTHER_LETTER: Final = """Gemeente Assen
Afdeling Burgerzaken
Uw aanvraag voor een parkeervergunning is ontvangen. Wij behandelen uw aanvraag binnen
acht weken. U hoeft nu niets te doen. Zodra wij een besluit hebben genomen, ontvangt u
daarover een brief. Met vriendelijke groet, het college van burgemeester en wethouders.
"""

_CONFUSIONS: Final = {
    "e": "c",
    "a": "o",
    "i": "l",
    "n": "u",
    "o": "a",
    "r": "n",
    "t": "f",
    "s": "z",
    "l": "i",
    "d": "b",
}


def rescan(text: str, every: int = 50) -> str:
    """`text` as a second scan reads it: every `every`-th letter misread (50: 2%). Digits
    stay as they are."""
    out = []
    seen = 0
    for char in text:
        if char.isalpha():
            seen += 1
            if seen % every == 0:
                out.append(_CONFUSIONS.get(char.lower(), "x"))
                continue
        out.append(char)
    return "".join(out)


def facts_answer(letter_date: str, date_quote: str, amount: str, amount_quote: str) -> str:
    """An AI answer with a strong letter date and a strong invoice (number and amount)."""
    return json.dumps(
        {
            "afzender": {"naam": "Energie Noord BV", "bron": "Energie Noord BV", "zekerheid": 0.95},
            "documenttype": "factuur",
            "dagtekening": {"waarde": letter_date, "bron": date_quote, "zekerheid": 0.95},
            "samenvatting_b1": "Je jaarafrekening van de energie.",
            "acties": [],
            "factuur": {
                "nummer": "F2026-018834",
                "vervaldatum": None,
                "bedrag_incl": amount,
                "bron": amount_quote,
                "zekerheid": 0.95,
            },
            "op_naam_van": [],
            "labels": [],
        }
    )


class Saying:
    """A provider that always answers `answer`."""

    name = "fake"
    model = "fake-1"

    def __init__(self, answer: str) -> None:
        self.answer = copy.copy(answer)

    def extract(self, request: ExtractionRequest) -> str:
        return self.answer


def letter(
    workspace: Workspace,
    text: str,
    *,
    status: str = Document.Status.NEEDS_REVIEW,
    days_ago: int = 0,
    **fields: object,
) -> Document:
    """A letter that is already there, written as the system."""
    with workspace_context(workspace.id, actor=SYSTEM):
        document = Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text=text,
            status=status,
            **fields,
        )
        if days_ago:
            Document.objects.filter(id=document.id).update(
                created_at=timezone.now() - timedelta(days=days_ago)
            )
        return Document.objects.get(id=document.id)


def arrive(workspace: Workspace, text: str, provider: Saying | None = None) -> Document:
    """A new letter through the pipeline; the stored row afterwards."""
    document = letter(workspace, text, status=Document.Status.PROCESSING)
    run_pipeline(
        workspace.id, document.id, provider_factory=lambda _: provider, today=lambda: TODAY
    )
    return fresh(document)


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def pairs_of(workspace: Workspace) -> list[DuplicateCandidate]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(DuplicateCandidate.objects.order_by("created_at", "id"))
