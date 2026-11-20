from collections.abc import Sequence
from uuid import UUID

from django.db import IntegrityError, connection, models, transaction
from django.db.models.functions import Lower
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.names import check_shown
from apps.common.privacy import mask_bsns
from apps.common.text import has_direction_control, is_visible
from apps.labels.models import COLORS, Label
from apps.rules.definition import named_ids, only_adds_label
from apps.rules.models import Rule
from apps.workspaces.context import system_actor
from apps.workspaces.models import Workspace

_NAME_UNIQUE = "labels_label_name_unique"

NAME_MAX = 50


def _labels(workspace: Workspace) -> "models.QuerySet[Label]":
    """The workspace's labels, each annotated with `document_count`."""
    return Label.objects.filter(workspace=workspace).annotate(
        document_count=models.Count("document_links")
    )


def list_labels(workspace: Workspace) -> list[Label]:
    """Labels by name; needs the workspace's RLS context.

    `document_count` from one grouped count over the label links (each through its
    policy, the visible letters hashed once), not a join of every label to every link
    (measured, DECISIONS 2026-11-20). Same count: the links the policy shows, of letters
    not in the trash.
    """
    labels = list(Label.objects.filter(workspace=workspace).order_by(Lower("name"), "id"))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT l.label_id, pg_catalog.count(*) FROM public.documents_documentlabel l"
            " JOIN public.documents_document d ON d.id = l.document_id AND d.trashed_at IS NULL"
            " WHERE l.workspace_id = %s GROUP BY l.label_id",
            [workspace.id],
        )
        counts: dict[UUID, int] = {row[0]: int(row[1]) for row in cursor.fetchall()}
    for label in labels:
        label.document_count = counts.get(label.id, 0)  # type: ignore[attr-defined]
    return labels


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
    check_shown(cleaned, empty=_("Een label heeft een naam nodig."))
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


# At most this many labels per workspace, made by hand (security review 2026-11-20): the
# AI gets only the first 100, so a flood of labels pushes the real ones out of its list.
MAX_LABELS = 200


def create_label(*, workspace: Workspace, name: str, color: str) -> Label:
    with transaction.atomic():
        if Label.objects.filter(workspace=workspace).count() >= MAX_LABELS:
            raise ApiError(
                409,
                "too_many_labels",
                _("Deze werkruimte heeft het grootste aantal labels. Verwijder er eerst een."),
            )
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


def delete_label(*, workspace: Workspace, label_id: UUID, actor: AuditActor) -> None:
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
        # Imported here: organise imports this module (label_not_found).
        from apps.documents import organise

        # Checked as the caller above; the label leaves every letter, also those the
        # caller cannot see (a hidden link would otherwise fail the commit).
        with system_actor():
            for rule in own_rules:
                rule.delete()
            organise.record_label_deleted(label, actor_id=actor.user_id)
            label.delete()
        # A label's name is the user's own words: the row carries the id only.
        record(
            AuditCode.LABEL_DELETED,
            actor=actor,
            target=AuditTarget(TargetKind.LABEL, label_id),
            workspace_id=workspace.id,
        )


def create_extra_labels(*, workspace: Workspace, names: Sequence[str]) -> None:
    """Own labels for these names; blanks, repeats and taken names (ignoring case) are skipped."""
    with transaction.atomic():
        taken = {
            n.lower()
            for n in Label.objects.filter(workspace=workspace).values_list("name", flat=True)
        }
        for raw in names:
            name = clean_name(raw)[:NAME_MAX]
            if not is_visible(name) or has_direction_control(name) or name.lower() in taken:
                continue
            taken.add(name.lower())
            try:
                _save(Label(workspace=workspace, name=name))
            except ApiError:
                # Taken after all: PostgreSQL's lower() differs from Python's for a few
                # letters (the unique index uses PostgreSQL's).
                continue
