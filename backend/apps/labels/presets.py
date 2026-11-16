"""The start sets of labels per workspace kind (design 7.3).

`recognise` becomes one rule per label (mode `propose`, match `any`) once the rules
engine exists; until then it is data only.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.db import transaction
from django.db.models.functions import Lower

from apps.labels.models import Label
from apps.labels.services import list_labels
from apps.workspaces.models import Workspace

# The operators each condition field allows (design 9.2). The rules engine will own
# this table; it lives here until that engine exists.
CONDITION_OPERATORS: Mapping[str, frozenset[str]] = {
    "text": frozenset({"contains_word", "contains", "not_contains_word"}),
    "sender": frozenset({"contains_word", "contains", "equals"}),
    "document_type": frozenset({"is", "is_not"}),
    "label": frozenset({"has", "has_not"}),
    "addressee": frozenset({"is", "is_empty"}),
    "source": frozenset({"is"}),
}


@dataclass(frozen=True)
class Recognise:
    field: str
    op: str
    value: str


@dataclass(frozen=True)
class PresetLabel:
    key: str
    name: str
    color: str
    recognise: tuple[Recognise, ...]


def _words(*words: str) -> tuple[Recognise, ...]:
    return tuple(Recognise("text", "contains_word", w) for w in words)


def _sender_contains(value: str) -> Recognise:
    return Recognise("sender", "contains", value)


def _type_is(code: str) -> Recognise:
    return Recognise("document_type", "is", code)


_HOME = (
    PresetLabel(
        "belastingdienst",
        "Belastingdienst",
        "blue",
        _words("belastingdienst", "toeslagen", "aanslag"),
    ),
    PresetLabel(
        "zorg",
        "Zorg",
        "red",
        _words(
            "zorgverzekering",
            "zorgverzekeraar",
            "CAK",
            "huisarts",
            "ziekenhuis",
            "apotheek",
            "tandarts",
        ),
    ),
    PresetLabel(
        "verzekering",
        "Verzekering",
        "purple",
        _words("polis", "polisblad", "verzekering", "verzekeringen"),
    ),
    PresetLabel(
        "wonen", "Wonen", "green", _words("hypotheek", "huur", "huurovereenkomst", "WOZ", "VvE")
    ),
    PresetLabel(
        "energie",
        "Energie en water",
        "yellow",
        _words("energie", "gas", "stroom", "waterbedrijf", "drinkwater"),
    ),
    PresetLabel(
        "auto",
        "Auto",
        "teal",
        _words("APK", "RDW", "kenteken", "wegenbelasting", "motorrijtuigenbelasting"),
    ),
    PresetLabel(
        "bank",
        "Bank",
        "grey",
        (_sender_contains("bank"), *_words("rekeningafschrift", "creditcard")),
    ),
    PresetLabel(
        "inkomen",
        "Inkomen",
        "green",
        _words("loonstrook", "jaaropgave", "UWV", "SVB", "pensioen"),
    ),
    PresetLabel(
        "gemeente",
        "Gemeente",
        "orange",
        _words("gemeente", "gemeentelijke belastingen", "waterschap"),
    ),
    PresetLabel(
        "abonnementen",
        "Abonnementen",
        "teal",
        (Recognise("text", "contains", "abonnement"), *_words("contributie", "lidmaatschap")),
    ),
)

_BUSINESS = (
    PresetLabel(
        "belastingdienst",
        "Belastingdienst",
        "blue",
        _words("belastingdienst", "aanslag", "naheffing", "aangifte"),
    ),
    PresetLabel("inkoop", "Inkoop", "orange", (_type_is("invoice"),)),
    PresetLabel("verkoop", "Verkoop", "green", _words("offerte", "opdrachtbevestiging")),
    PresetLabel(
        "bank",
        "Bank",
        "grey",
        (_sender_contains("bank"), *_words("rekeningafschrift", "creditcard")),
    ),
    PresetLabel(
        "personeel",
        "Personeel",
        "purple",
        _words("loonstrook", "loonheffing", "pensioenfonds", "UWV", "arbo"),
    ),
    PresetLabel(
        "verzekering",
        "Verzekering",
        "purple",
        _words("polis", "polisblad", "verzekering", "verzekeringen"),
    ),
    PresetLabel(
        "contracten",
        "Contracten",
        "teal",
        (_type_is("contract"), *_words("overeenkomst", "algemene voorwaarden")),
    ),
    PresetLabel(
        "kvk",
        "KVK en overheid",
        "grey",
        (
            Recognise("sender", "contains_word", "KVK"),
            Recognise("sender", "contains_word", "RVO"),
            Recognise("sender", "contains_word", "Kamer van Koophandel"),
            _sender_contains("gemeente"),
        ),
    ),
    PresetLabel(
        "huisvesting", "Huisvesting", "yellow", _words("huur", "huurovereenkomst", "energie", "VvE")
    ),
)

PRESETS: Mapping[str, tuple[PresetLabel, ...]] = {
    Workspace.Kind.HOME: _HOME,
    Workspace.Kind.BUSINESS: _BUSINESS,
}


def apply_preset(workspace: Workspace) -> list[Label]:
    """Create the preset labels that are missing; return all labels (as `list_labels`).

    A preset label counts as present when a label has its `system_key`, or else its
    name (ignoring case); an existing label is never changed. Idempotent. Needs the
    workspace's RLS context.
    """
    with transaction.atomic():
        existing = Label.objects.filter(workspace=workspace)
        keys = set(existing.exclude(system_key="").values_list("system_key", flat=True))
        names = set(existing.annotate(lower=Lower("name")).values_list("lower", flat=True))
        for preset in PRESETS[workspace.kind]:
            if preset.key in keys or preset.name.lower() in names:
                continue
            Label.objects.create(
                workspace=workspace, name=preset.name, color=preset.color, system_key=preset.key
            )
        return list_labels(workspace)
