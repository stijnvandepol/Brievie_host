from collections.abc import Sequence
from uuid import UUID

from django.db import IntegrityError, models, transaction
from django.db.models.functions import Lower
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.labels.models import COLORS, Label
from apps.rules.definition import named_ids, only_adds_label
from apps.rules.models import Rule
from apps.workspaces.models import Workspace

_NAME_UNIQUE = "labels_label_name_unique"

NAME_MAX = 50


def _labels(workspace: Workspace) -> "models.QuerySet[Label]":
    """The workspace's labels, each annotated with `document_count`."""
    return Label.objects.filter(workspace=workspace).annotate(
        document_count=models.Count("document_links")
    )


def list_labels(workspace: Workspace) -> list[Label]:
    """Labels by name; needs the workspace's RLS context."""
    return list(_labels(workspace).order_by(Lower("name"), "id"))


def _get_for_update(workspace: Workspace, label_id: UUID) -> Label:
    label = Label.objects.select_for_update().filter(workspace=workspace, id=label_id).first()
    if label is None:
        raise label_not_found()
    return label


def label_not_found() -> ApiError:
    return ApiError(404, "label_not_found", _("We kunnen dit label niet vinden."))


def clean_name(name: str) -> str:
    """Masked and trimmed; empty when nothing is left."""
    return mask_bsns(name).strip()


def _valid_name(name: str) -> str:
    cleaned = clean_name(name)
    if not cleaned:
        raise ApiError(422, "validation_error", _("Een label heeft een naam nodig."))
    if len(cleaned) > NAME_MAX:
        raise ApiError(
            422,
            "validation_error",
            _("Een labelnaam is hoogstens %(max)d tekens.") % {"max": NAME_MAX},
        )
    return cleaned


def _valid_color(color: str) -> str:
    if color not in COLORS:
        raise ApiError(422, "validation_error", _("Deze kleur kennen we niet."))
    return color


def _save(label: Label) -> None:
    """Save, turning a taken name into the API error that explains it."""
    try:
        with transaction.atomic():
            label.save()
    except IntegrityError as exc:
        cause = exc.__cause__
        if (
            isinstance(cause, pg_errors.UniqueViolation)
            and cause.diag.constraint_name == _NAME_UNIQUE
        ):
            raise ApiError(409, "label_exists", _("Er is al een label met deze naam.")) from exc
        raise


def create_label(*, workspace: Workspace, name: str, color: str) -> Label:
    with transaction.atomic():
        label = Label(workspace=workspace, name=_valid_name(name), color=_valid_color(color))
        _save(label)
        return _labels(workspace).get(id=label.id)


def update_label(*, workspace: Workspace, label_id: UUID, name: str, color: str) -> Label:
    """An empty `color` keeps the label's colour."""
    with transaction.atomic():
        label = _get_for_update(workspace, label_id)
        label.name = _valid_name(name)
        if color:
            label.color = _valid_color(color)
        _save(label)
        return _labels(workspace).get(id=label.id)


def delete_label(*, workspace: Workspace, label_id: UUID) -> None:
    """Removes the label from its documents too; their events keep its name.

    The label's preset rule goes with it when adding this label is all it does. Another
    rule that names the label is a conflict: the rule would point at nothing.
    """
    with transaction.atomic():
        # Rules first, in id order: a running rule holds its rules and then needs the label.
        rules = list(Rule.objects.select_for_update().filter(workspace=workspace).order_by("id"))
        label = _get_for_update(workspace, label_id)
        own_rules: list[Rule] = []
        for rule in rules:
            if label.id not in named_ids(rule)[0]:
                continue
            if (
                label.system_key
                and rule.system_key == label.system_key
                and only_adds_label(rule, label.id)
            ):
                own_rules.append(rule)
                continue
            raise ApiError(
                409,
                "label_in_use",
                _("De regel '%(rule)s' gebruikt dit label. Pas die regel eerst aan.")
                % {"rule": rule.name},
            )
        for rule in own_rules:
            rule.delete()
        label.delete()


def create_extra_labels(*, workspace: Workspace, names: Sequence[str]) -> None:
    """Own labels for these names; blanks, repeats and taken names (ignoring case) are skipped."""
    with transaction.atomic():
        taken = {
            n.lower()
            for n in Label.objects.filter(workspace=workspace).values_list("name", flat=True)
        }
        for raw in names:
            name = clean_name(raw)[:NAME_MAX]
            if not name or name.lower() in taken:
                continue
            taken.add(name.lower())
            try:
                _save(Label(workspace=workspace, name=name))
            except ApiError:
                # Taken after all: PostgreSQL's lower() differs from Python's for a few
                # letters (the unique index uses PostgreSQL's).
                continue
