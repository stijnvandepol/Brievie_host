"""The start sets of labels per workspace kind (design 7.3).

`recognise` becomes one rule per label (mode `propose`, match `any`), created with the
label by `apply_preset` (see `apps.rules.presets`).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.db import transaction
from django.db.models.functions import Lower
from django.utils import translation
from django.utils.translation import gettext_noop as _

from apps.labels.models import Label
from apps.labels.services import list_labels
from apps.workspaces.models import Workspace


@dataclass(frozen=True)
class Recognise:
    field: str
    op: str
    value: str


@dataclass(frozen=True)
class PresetLabel:
    key: str
    # The Dutch name, marked for the catalogue: a label is created with the name in the
    # language of the request that creates it (`display_name`) and keeps it.
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
        _("Belastingdienst"),
        "blue",
        # Not "aanslag": the municipality and the water board send an aanslag too.
        _words("belastingdienst", "toeslagen"),
    ),
    PresetLabel(
        "zorg",
        _("Zorg"),
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
        _("Verzekering"),
        "purple",
        _words("polis", "polisblad", "verzekering", "verzekeringen"),
    ),
    PresetLabel(
        "wonen", _("Wonen"), "green", _words("hypotheek", "huur", "huurovereenkomst", "WOZ", "VvE")
    ),
    PresetLabel(
        "energie",
        _("Energie en water"),
        "yellow",
        _words("energie", "gas", "stroom", "waterbedrijf", "drinkwater"),
    ),
    PresetLabel(
        "auto",
        _("Auto"),
        "teal",
        _words("APK", "RDW", "kenteken", "wegenbelasting", "motorrijtuigenbelasting"),
    ),
    PresetLabel(
        "bank",
        _("Bank"),
        "grey",
        (_sender_contains("bank"), *_words("rekeningafschrift", "creditcard")),
    ),
    PresetLabel(
        "inkomen",
        _("Inkomen"),
        "green",
        _words("loonstrook", "jaaropgave", "UWV", "SVB", "pensioen"),
    ),
    PresetLabel(
        "gemeente",
        _("Gemeente"),
        "orange",
        _words("gemeente", "gemeentelijke belastingen", "waterschap"),
    ),
    PresetLabel(
        "abonnementen",
        _("Abonnementen"),
        "teal",
        (Recognise("text", "contains", "abonnement"), *_words("contributie", "lidmaatschap")),
    ),
)

_BUSINESS = (
    PresetLabel(
        "belastingdienst",
        _("Belastingdienst"),
        "blue",
        _words("belastingdienst", "naheffing", "aangifte"),
    ),
    PresetLabel("inkoop", _("Inkoop"), "orange", (_type_is("invoice"),)),
    PresetLabel("verkoop", _("Verkoop"), "green", _words("offerte", "opdrachtbevestiging")),
    PresetLabel(
        "bank",
        _("Bank"),
        "grey",
        (_sender_contains("bank"), *_words("rekeningafschrift", "creditcard")),
    ),
    PresetLabel(
        "personeel",
        _("Personeel"),
        "purple",
        _words("loonstrook", "loonheffing", "pensioenfonds", "UWV", "arbo"),
    ),
    PresetLabel(
        "verzekering",
        _("Verzekering"),
        "purple",
        _words("polis", "polisblad", "verzekering", "verzekeringen"),
    ),
    PresetLabel(
        "contracten",
        _("Contracten"),
        "teal",
        (_type_is("contract"),),
    ),
    PresetLabel(
        "kvk",
        _("KVK en overheid"),
        "grey",
        (
            Recognise("sender", "contains_word", "KVK"),
            Recognise("sender", "contains_word", "RVO"),
            Recognise("sender", "contains_word", "Kamer van Koophandel"),
            _sender_contains("gemeente"),
        ),
    ),
    PresetLabel(
        "huisvesting",
        _("Huisvesting"),
        "yellow",
        _words("huur", "huurovereenkomst", "energie", "VvE"),
    ),
)


def display_name(preset: PresetLabel) -> str:
    """The preset's name in the active language."""
    return translation.gettext(preset.name)


PRESETS: Mapping[str, tuple[PresetLabel, ...]] = {
    Workspace.Kind.HOME: _HOME,
    Workspace.Kind.BUSINESS: _BUSINESS,
}


def apply_preset(workspace: Workspace) -> list[Label]:
    """Create the preset labels that are missing, and the preset rules of the preset
    labels that have none; return all labels (as `list_labels`).

    A preset label counts as present when a label has its `system_key`, or else its
    name (ignoring case); an existing label is never changed. Idempotent. Needs the
    workspace's RLS context.
    """
    # rules.presets reads PRESETS from this module, so it is imported here.
    from apps.rules.presets import ensure_preset_rules

    with transaction.atomic():
        # Two calls at once would both create the same labels; the second waits here.
        Workspace.objects.select_for_update().filter(id=workspace.id).first()
        existing = Label.objects.filter(workspace=workspace)
        keys = set(existing.exclude(system_key="").values_list("system_key", flat=True))
        names = set(existing.annotate(lower=Lower("name")).values_list("lower", flat=True))
        for preset in PRESETS[workspace.kind]:
            name = display_name(preset)
            if preset.key in keys or name.lower() in names:
                continue
            Label.objects.create(
                workspace=workspace, name=name, color=preset.color, system_key=preset.key
            )
        ensure_preset_rules(workspace)
        return list_labels(workspace)
