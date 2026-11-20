"""The Dutch sentence for a request the schema refused (422 `validation_error`).

A schema's limits (`max_length`, a `Literal`, a date) refuse a request before a
service sees it, so the services' own sentences never showed and every answer was
"De gegevens kloppen niet". This builds a sentence from the first error: what kind of
problem, the limit, and, for a field it knows, what the field is called in Dutch.
"""

from collections.abc import Mapping, Sequence

from django.utils.translation import gettext as _

# Field name -> the Dutch noun with "een", for a text that is too long or empty.
_TEXTS: Mapping[str, str] = {
    "name": "een naam",
    "display_name": "een naam",
    "workspace_name": "een naam",
    "device_name": "een naam",
    "sender": "een afzender",
    "summary": "een samenvatting",
    "description": "een omschrijving",
    "note": "een notitie",
    "email": "een e-mailadres",
    "password": "een wachtwoord",
    "new_password": "een wachtwoord",
    "current_password": "een wachtwoord",
    "value": "een voorwaarde",
}
# Field name -> the Dutch plural, for a list with too many or too few items.
_LISTS: Mapping[str, str] = {
    "conditions": "voorwaarden",
    "actions": "acties",
    "tasks": "taken",
    "label_ids": "labels",
    "addressee_ids": "personen",
    "person_ids": "personen",
    "name_variants": "schrijfwijzen",
    "rule_ids": "regels",
    "notice_ids": "meldingen",
    "extra_labels": "labels",
}
_DATES = frozenset(
    {"date_parsing", "date_from_datetime_parsing", "date_from_datetime_inexact", "date_type"}
)


def _choices() -> Mapping[str, str]:
    """Field name -> what to choose, for a value outside a fixed list."""
    return {
        "role": _("Kies een rol: beheerder, lid of meekijker."),
        "expires_in_days": _("Een uitnodiging is 1, 7 of 30 dagen geldig."),
        "workspace_kind": _("Kies Thuis of Zakelijk."),
    }


def generic() -> str:
    return _("De gegevens kloppen niet. Controleer wat je hebt ingevuld.")


def _field(error: Mapping[str, object]) -> str:
    """The last field name in the error's location (list positions skipped)."""
    location = error.get("loc", ())
    names = (
        [part for part in location if isinstance(part, str)]
        if isinstance(location, tuple | list)
        else []
    )
    return names[-1] if names else ""


def _limit(error: Mapping[str, object], key: str) -> int | None:
    context = error.get("ctx")
    value = context.get(key) if isinstance(context, Mapping) else None
    return value if isinstance(value, int) else None


def message_for(errors: Sequence[Mapping[str, object]]) -> str:
    """One Dutch sentence for the first of Pydantic's `errors`."""
    if not errors:
        return generic()
    error = errors[0]
    kind = str(error.get("type", ""))
    field = _field(error)
    text = _TEXTS.get(field)
    plural = _LISTS.get(field)
    if kind == "string_too_long" and text and (high := _limit(error, "max_length")):
        return _("Dit is te lang: %(field)s heeft hoogstens %(max)d tekens.") % {
            "field": text,
            "max": high,
        }
    if kind in ("string_too_short", "missing") and text:
        low = _limit(error, "min_length")
        if low is None or low <= 1:
            return _("Vul %(field)s in.") % {"field": text}
        return _("Dit is te kort: %(field)s heeft minstens %(min)d tekens.") % {
            "field": text,
            "min": low,
        }
    if kind == "too_long" and plural and (high := _limit(error, "max_length")) is not None:
        return _("Dit zijn er te veel: hoogstens %(max)d %(items)s.") % {
            "max": high,
            "items": plural,
        }
    if kind == "too_short" and plural and (low := _limit(error, "min_length")) is not None:
        return _("Dit zijn er te weinig: minstens %(min)d %(items)s.") % {
            "min": low,
            "items": plural,
        }
    if kind in ("literal_error", "enum") and field in _choices():
        return _choices()[field]
    if kind in _DATES:
        return _("Deze datum bestaat niet. Schrijf hem als jjjj-mm-dd.")
    if kind in ("uuid_parsing", "uuid_type", "uuid_version"):
        return _("Een id klopt niet.")
    return generic()
