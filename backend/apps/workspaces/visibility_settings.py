"""Changing who sees what, safely (spec S 4.4, 4.13, 4.14).

The switches and the kind change through `change`; roles, sorting flags, department
members and person links change through their own writers (`workspaces.members`,
`people.services`). Every one of them takes `lock_memberships` first and then runs the
same steps through `sight_change`: a snapshot before, its own write, a snapshot after,
`visibility_rules.diff`, `require_confirmation` (409 when somebody gains without a
confirmed, current preview), its audit row, `notify_changes`, and the release of every
assignment whose assignee no longer sees the letter (switching off ends what ran under it).

`preview` runs any of those writes inside a savepoint that is rolled back, with the
confirmation and the notices left out, and answers who gains, who loses, how many letters
change viewers, and the token a confirmation must send back.

Runs as the system where it reads the tables (`apps/workspaces/visibility_settings.py` is
in the closed list of `tests/test_actor_callers.py`): it answers about letters and people
the admin may not see, with counts and names of people only.
"""

import hashlib
import json
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Final
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.documents.models import Document, DocumentReader
from apps.people.models import DepartmentMember, Person
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.locks import lock_memberships
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.policy_text import PolicyFacts, category_phrase, describe
from apps.workspaces.presets import ADMIN_PRESET_FIELDS, PRESETS
from apps.workspaces.visibility_rules import (
    ALL_AS_ADMIN,
    Change,
    MemberFacts,
    PolicySnapshot,
    Switches,
    diff,
    needs_confirmation,
    split_category,
)
from apps.workspaces.visibility_sql import viewer_groups_sql, viewer_pairs_sql

# --- the snapshot ---------------------------------------------------------------------


def snapshot(workspace: Workspace) -> PolicySnapshot:
    """The workspace's rule of sight now, read as the system: the switches, every member
    with role, flag, persons and departments, and the names of persons and departments."""
    switches_row = Workspace.objects.filter(id=workspace.id).values_list(
        "everyone_sees_all", "departments_enabled", "admins_read_all"
    )[0]
    memberships = list(
        Membership.objects.filter(workspace_id=workspace.id)
        .order_by("user_id")
        .values_list("user_id", "user__name", "role", "sorts_post")
    )
    with workspace_context(workspace.id, actor=SYSTEM):
        persons = list(
            Person.objects.filter(workspace_id=workspace.id).values_list(
                "id", "kind", "user_id", "display_name"
            )
        )
        departments = list(
            DepartmentMember.objects.filter(workspace_id=workspace.id).values_list(
                "user_id", "department_id"
            )
        )
    linked: dict[UUID, set[UUID]] = {}
    for person_id, kind, user_id, _name in persons:
        if kind == Person.Kind.PERSON and user_id is not None:
            linked.setdefault(user_id, set()).add(person_id)
    member_of: dict[UUID, set[UUID]] = {}
    for user_id, department_id in departments:
        member_of.setdefault(user_id, set()).add(department_id)
    return PolicySnapshot(
        switches=Switches(*switches_row),
        members=tuple(
            MemberFacts(
                user_id=user_id,
                name=name,
                role=role,
                sorts_post=sorts_post,
                person_ids=frozenset(linked.get(user_id, ())),
                department_ids=frozenset(member_of.get(user_id, ())),
            )
            for user_id, name, role, sorts_post in memberships
        ),
        person_names={p: n for p, k, _u, n in persons if k == Person.Kind.PERSON},
        department_names={p: n for p, k, _u, n in persons if k == Person.Kind.DEPARTMENT},
    )


def fingerprint(policy: PolicySnapshot) -> str:
    """The preview token: a hash of everything a change of sight is computed from (the
    switches, every member's role, flag, persons and departments; S 4.4 point 2)."""
    canonical = {
        "switches": [
            policy.switches.everyone_sees_all,
            policy.switches.departments_enabled,
            policy.switches.admins_read_all,
        ],
        "members": sorted(
            [
                str(m.user_id),
                m.role,
                m.sorts_post,
                sorted(str(p) for p in m.person_ids),
                sorted(str(d) for d in m.department_ids),
            ]
            for m in policy.members
        ),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


# The purpose label of the preview token's key, derived from SECRET_KEY (no setting).
_KEY_PURPOSE: Final = "brievie.workspaces.visibility.preview-confirmation"


@dataclass(frozen=True)
class MemberChange:
    """A member change of sight; `kind` "" for none. Also what a preview of one is."""

    kind: str = ""
    user_id: UUID | None = None
    person_id: UUID | None = None
    role: str = ""
    sorts_post: bool = False


# The fields that make up each kind of member change (and so its token).
_MEMBER_CHANGE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "role": ("user_id", "role"),
    "sorts_post": ("user_id", "sorts_post"),
    "department_add": ("user_id", "person_id"),
    "department_remove": ("user_id", "person_id"),
    "link": ("user_id", "person_id"),
    "unlink": ("person_id",),
    "remove": ("user_id",),
    "invite_admin": (),
}


def member_change_key(change: MemberChange) -> str:
    """The change in a canonical form: its kind and only the fields that kind uses."""
    fields = {name: getattr(change, name) for name in _MEMBER_CHANGE_FIELDS[change.kind]}
    return json.dumps({"member_change": change.kind, **fields}, sort_keys=True, default=str)


def confirmation_token(
    *, workspace_id: UUID, actor_id: UUID | None, change: str, before: PolicySnapshot
) -> str:
    """The token a confirmation sends back: an HMAC (key derived from SECRET_KEY) over the
    workspace, the admin who previewed, the change itself and the state it was computed
    from. It confirms exactly that change, by that admin, in that state; nobody can make
    one without the server."""
    value = json.dumps([str(workspace_id), str(actor_id or ""), change, fingerprint(before)])
    return salted_hmac(_KEY_PURPOSE, value, algorithm="sha256").hexdigest()


# --- confirmation and notices ---------------------------------------------------------


class _Previewing(threading.local):
    def __init__(self) -> None:
        self.depth = 0


_previewing = _Previewing()


def previewing() -> bool:
    """Inside `preview`: nothing is confirmed, told or kept."""
    return _previewing.depth > 0


def confirmation_required() -> ApiError:
    return ApiError(
        409,
        "confirmation_required",
        _("Bevestig eerst dat deze mensen meer post gaan zien."),
    )


def preview_outdated() -> ApiError:
    return ApiError(
        409,
        "preview_outdated",
        _("Er is intussen iets veranderd. Bekijk de wijziging opnieuw."),
    )


def require_confirmation(
    *,
    changes: Sequence[Change],
    before: PolicySnapshot,
    narrowing: bool,
    confirmed: bool,
    preview_token: str,
    workspace_id: UUID,
    actor_id: UUID | None,
    change: str,
) -> None:
    """409 `confirmation_required` when somebody gains sight and the admin did not
    confirm; 409 `preview_outdated` when the token was not given for this change by this
    admin in this workspace in the state `before` (another admin changed something in
    between, or the token belongs to another preview)."""
    if previewing() or not needs_confirmation(changes, before=before, narrowing=narrowing):
        return
    if not confirmed:
        raise confirmation_required()
    expected = confirmation_token(
        workspace_id=workspace_id, actor_id=actor_id, change=change, before=before
    )
    if not constant_time_compare(preview_token, expected):
        raise preview_outdated()


def _notice_values(category: str, names: PolicySnapshot) -> dict[str, str]:
    kind, item = split_category(category)
    values = {"category": kind}
    if kind == "department" and item is not None:
        values["department"] = names.department_names.get(item, "")
    elif kind == "person" and item is not None:
        values["person"] = names.person_names.get(item, "")
    return values


def _names(before: PolicySnapshot, after: PolicySnapshot) -> PolicySnapshot:
    """`after` with the names of `before` it lost (a person deleted in between)."""
    return PolicySnapshot(
        switches=after.switches,
        members=after.members,
        person_names={**before.person_names, **after.person_names},
        department_names={**before.department_names, **after.department_names},
    )


def notify_changes(
    workspace: Workspace,
    changes: Sequence[Change],
    *,
    names: PolicySnapshot,
    actor_name: str,
    admins_gained_all: bool,
    skip_user_id: UUID | None = None,
) -> None:
    """Content-free notices (S 4.4 point 4): `sight_widened` to each member who gains,
    `sight_narrowed` to each who loses (one each, the broadest kind of post), and
    `admins_read_all_on` to every member when admins gained all post (R37). Nothing to
    `skip_user_id` (a member who just joined)."""
    from apps.accounts.notices import notify  # notices import services that import this

    if previewing():
        return
    # A member who left is not told what they no longer see here.
    members = {m.user_id for m in names.members}
    users = {
        u.id: u
        for u in User.objects.filter(id__in=[c.user_id for c in changes if c.user_id in members])
        if u.id != skip_user_id
    }
    for change in changes:
        user = users.get(change.user_id)
        if user is None:
            continue
        if change.gains:
            notify(
                user,
                "sight_widened",
                actor_name=actor_name,
                values=_notice_values(change.gains[0], names),
            )
        if change.loses:
            notify(
                user,
                "sight_narrowed",
                actor_name=actor_name,
                values=_notice_values(change.loses[0], names),
            )
    if admins_gained_all:
        for member in User.objects.filter(memberships__workspace=workspace).order_by("id"):
            if member.id != skip_user_id:
                notify(member, "admins_read_all_on", actor_name=actor_name)


def notify_join(
    workspace: Workspace, *, before: PolicySnapshot, user_id: UUID, actor_name: str
) -> None:
    """A member joined through an invitation (inside the accept's transaction): the
    notices of S 4.4 to everybody else whose sight changed by it."""
    after = snapshot(workspace)
    changes = diff(before, after)
    notify_changes(
        workspace,
        changes,
        names=_names(before, after),
        actor_name=actor_name,
        admins_gained_all=any(ALL_AS_ADMIN in c.gains for c in changes),
        skip_user_id=user_id,
    )


def notify_self_grant(
    users: Sequence[User], *, category: str, names: PolicySnapshot, actor_name: str
) -> None:
    """S 4.7: an admin gave themselves sight; `users` (the department's members, or the
    other admins) are told."""
    from apps.accounts.notices import notify

    if previewing():
        return
    for user in users:
        notify(user, "self_grant", actor_name=actor_name, values=_notice_values(category, names))


def _release(workspace_id: UUID) -> None:
    """An assignment ends with sight of its letter (Mijn taken S 5.3)."""
    from apps.tasks.assignment import release_unseeing_assignees

    with workspace_context(workspace_id, actor=SYSTEM):
        release_unseeing_assignees(workspace_id=workspace_id)


@dataclass
class SightChange:
    """What `sight_change` found: the snapshots and the changes per member."""

    before: PolicySnapshot
    after: PolicySnapshot | None = None
    changes: list[Change] = field(default_factory=list)


@contextmanager
def sight_change(
    workspace: Workspace,
    *,
    narrowing: bool,
    confirmed: bool,
    preview_token: str,
    actor: AuditActor,
    change: str,
    ask_confirmation: bool = True,
) -> Iterator[SightChange]:
    """The steps of S 4.4 around one write: the caller holds `lock_memberships`, writes
    (and audits) inside the block; on the way out the change is diffed, confirmed (unless
    `ask_confirmation` is False: a member's removal, S 4.4's "no"), told, and every
    assignment that rested on lost sight released. Raises inside the caller's transaction,
    so a refused confirmation undoes the write."""
    result = SightChange(before=snapshot(workspace))
    yield result
    after = snapshot(workspace)
    result.after = after
    result.changes = diff(result.before, after)
    if ask_confirmation:
        require_confirmation(
            changes=result.changes,
            before=result.before,
            narrowing=narrowing,
            confirmed=confirmed,
            preview_token=preview_token,
            workspace_id=workspace.id,
            actor_id=actor.user_id,
            change=change,
        )
    notify_changes(
        workspace,
        result.changes,
        names=_names(result.before, after),
        actor_name=actor.name,
        admins_gained_all=any(ALL_AS_ADMIN in c.gains for c in result.changes),
    )
    if any(c.loses for c in result.changes):
        _release(workspace.id)


# --- the preview ----------------------------------------------------------------------


def _letters_with_readers(workspace_id: UUID) -> list[UUID]:
    """Letters with an unexpired reader row (private ones, shares, emergency): asked
    one by one, the others in groups of alike letters (ruling M0). As the system: the
    admin may not see every reader row."""
    with workspace_context(workspace_id, actor=SYSTEM):
        return list(
            DocumentReader.objects.filter(workspace_id=workspace_id)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .values_list("document_id", flat=True)
            .distinct()
        )


ViewerMap = dict[object, tuple[int, frozenset[UUID]]]


@dataclass(frozen=True)
class _Counted:
    """The letters a preview may count for its admin: what they can read (finding 4)."""

    # None: every letter that is not private (the admin reads all of it).
    readable: list[UUID] | None
    # The letters with reader rows the admin can read, asked one by one.
    one_by_one: list[UUID]
    # Every letter with reader rows: never in a group.
    with_readers: list[UUID]


def _countable(workspace_id: UUID, actor_id: UUID | None) -> _Counted:
    """Only letters the admin can read now count (security review of PR #39, finding 4):
    a count of others would tell how much post a person or department has, private
    post included. Read through the policy, as the admin; the system counts everything."""
    with_readers = _letters_with_readers(workspace_id)
    if actor_id is None:
        return _Counted(readable=None, one_by_one=with_readers, with_readers=with_readers)
    with workspace_context(workspace_id, actor=actor_id), connection.cursor() as c:
        c.execute("SELECT public.brievie_sees_all_post(%s)", [actor_id])
        row = c.fetchone()
        sees_all = bool(row and row[0])
        # As the admin: the policy leaves out what they cannot read.
        letters = Document.objects.filter(workspace_id=workspace_id)
        one_by_one = set(letters.filter(id__in=with_readers).values_list("id", flat=True))
        readable = None if sees_all else sorted(letters.values_list("id", flat=True), key=str)
    return _Counted(
        readable=readable,
        one_by_one=[d for d in with_readers if d in one_by_one],
        with_readers=with_readers,
    )


def _viewer_map(workspace_id: UUID, counted: _Counted) -> ViewerMap:
    """Who sees what now: (letters, viewers) per group of alike letters, and per letter
    for the ones with reader rows; only letters the admin may count. As the system."""
    found: ViewerMap = {}
    one_by_one = counted.one_by_one
    readable = "" if counted.readable is None else " AND d.id = ANY(%s::uuid[])"
    params: list[UUID | list[UUID]] = [workspace_id, counted.with_readers]
    if counted.readable is not None:
        params.append(counted.readable)
    with workspace_context(workspace_id, actor=SYSTEM), connection.cursor() as c:
        c.execute(
            viewer_groups_sql("d.workspace_id = %s AND NOT (d.id = ANY(%s::uuid[]))" + readable),
            params,
        )
        for addressees, scanner, status, letters, viewers in c.fetchall():
            found[("group", tuple(addressees), scanner, status)] = (
                int(letters),
                frozenset(viewers),
            )
        c.execute(viewer_pairs_sql("d.id = ANY(%s::uuid[])"), [one_by_one])
        per_letter: dict[UUID, set[UUID]] = {d: set() for d in one_by_one}
        for document_id, user_id in c.fetchall():
            per_letter[document_id].add(user_id)
    for document_id, users in per_letter.items():
        found[("letter", document_id)] = (1, frozenset(users))
    return found


def _changed_letters(before: ViewerMap, after: ViewerMap) -> int:
    return sum(
        letters
        for key, (letters, viewers) in before.items()
        if after.get(key, (0, frozenset()))[1] != viewers
    )


@dataclass(frozen=True)
class PreviewResult:
    changes: list[Change]
    letter_count: int
    needs_confirmation: bool
    sentences: list[str]
    preview_token: str
    after: PolicySnapshot


def policy_facts(
    workspace: Workspace, policy: PolicySnapshot, viewer_id: UUID | None
) -> PolicyFacts:
    """Everything the sentences need beside the snapshot, read now."""
    from apps.backup.models import BackupRun
    from apps.pipeline import settings_service as ai_settings

    row = Workspace.objects.get(id=workspace.id)
    with workspace_context(workspace.id, actor=SYSTEM):
        ai = ai_settings.status(workspace.id)
    supplier = ""
    if ai.mode == "external" and ai.since is not None:
        preset = ai_settings.SUPPLIERS.get(ai.supplier or "")
        supplier = preset.name if preset is not None else (ai.host or "")
    return PolicyFacts(
        snapshot=policy,
        kind=row.kind,
        viewer_id=viewer_id,
        care_is_private=row.care_is_private,
        emergency_access=row.emergency_access,
        ai_supplier_name=supplier,
        ai_local=ai.mode == "local",
        has_backup=BackupRun.objects.filter(status=BackupRun.Status.SUCCEEDED).exists(),
        server_admin_names=tuple(
            User.objects.filter(is_server_admin=True, is_active=True)
            .order_by("name")
            .values_list("name", flat=True)
        ),
    )


def preview(
    workspace: Workspace,
    apply: Callable[[], object],
    *,
    narrowing: bool,
    actor_id: UUID | None,
    change: str,
    token_state: PolicySnapshot | None = None,
) -> PreviewResult:
    """Run `apply` (a change of sight) inside a savepoint that is rolled back, and say
    what it would do: who gains and who loses, how many letters the admin can read
    change viewers (no count of letters they cannot read, finding 4), whether
    the admin must confirm, the summary sentences of the state after, and the token a
    confirmation sends back (the fingerprint of the state now). Nothing is kept: no
    write, no audit row, no notice. Errors of `apply` (403, 404, 409, 422) pass through."""
    _previewing.depth += 1
    try:
        with transaction.atomic():
            lock_memberships(workspace)
            before = snapshot(workspace)
            counted = _countable(workspace.id, actor_id)
            before_map = _viewer_map(workspace.id, counted)
            apply()
            after = snapshot(workspace)
            after_map = _viewer_map(workspace.id, counted)
            sentences = describe(policy_facts(workspace, after, actor_id), audience="summary")
            transaction.set_rollback(True)
    finally:
        _previewing.depth -= 1
    changes = diff(before, after)
    return PreviewResult(
        changes=changes,
        letter_count=_changed_letters(before_map, after_map),
        needs_confirmation=needs_confirmation(changes, before=before, narrowing=narrowing),
        sentences=sentences,
        preview_token=confirmation_token(
            workspace_id=workspace.id,
            actor_id=actor_id,
            change=change,
            before=before if token_state is None else token_state,
        ),
        after=after,
    )


# --- the switches and the kind ----------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """A change of the switches or the kind; None leaves a value as it is."""

    kind: str | None = None
    everyone_sees_all: bool | None = None
    departments_enabled: bool | None = None
    admins_read_all: bool | None = None
    care_is_private: bool | None = None
    allow_member_export: bool | None = None
    # From the preset dialog: the kind's values where the proposal leaves None, and one
    # `workspace_preset_applied` row.
    apply_preset: bool = False
    # Also add the kind's start set of labels and rules (never removes anything).
    add_start_set: bool = False


SWITCH_FIELDS: Final = ("kind", *ADMIN_PRESET_FIELDS)


def proposal_key(proposal: Proposal) -> str:
    """The proposal in a canonical form (its token binds it)."""
    return json.dumps({"settings": asdict(proposal)}, sort_keys=True)


def _validate(proposal: Proposal) -> None:
    if proposal.kind is not None and proposal.kind not in Workspace.Kind.values:
        raise ApiError(422, "validation_error", _("Kies Thuis of Organisatie."))


def _apply_proposal(locked: Workspace, proposal: Proposal) -> dict[str, bool | str]:
    """Write the proposal on the locked row; the fields that really changed."""
    wanted: dict[str, bool | str] = {}
    if proposal.kind is not None:
        wanted["kind"] = proposal.kind
    if proposal.apply_preset:
        preset = PRESETS[proposal.kind or locked.kind]
        for name in ADMIN_PRESET_FIELDS:
            wanted[name] = getattr(preset, name)
    for name in ADMIN_PRESET_FIELDS:
        value = getattr(proposal, name)
        if value is not None:
            wanted[name] = value
    changed = {name: value for name, value in wanted.items() if getattr(locked, name) != value}
    for name, value in changed.items():
        setattr(locked, name, value)
    if changed:
        locked.save(update_fields=[*changed, "updated_at"])
    return changed


def _forbidden() -> ApiError:
    return ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))


def _require_admin(locked: list[Membership], actor: AuditActor) -> None:
    own = next((m for m in locked if m.user_id == actor.user_id), None)
    if own is None or own.role != Membership.Role.ADMIN:
        raise _forbidden()


def preview_change(*, workspace: Workspace, proposal: Proposal, actor_id: UUID) -> PreviewResult:
    """What `change` would do with this proposal, without doing it."""
    _validate(proposal)

    def apply() -> None:
        locked = Workspace.objects.select_for_update().get(id=workspace.id)
        _apply_proposal(locked, proposal)

    return preview(
        workspace, apply, narrowing=False, actor_id=actor_id, change=proposal_key(proposal)
    )


def change(
    *,
    workspace: Workspace,
    proposal: Proposal,
    actor: AuditActor,
    confirmed: bool,
    preview_token: str,
) -> Workspace:
    """Change the switches and the kind (S 4.4): admin by the locked memberships (403),
    a confirmed and current preview when anybody gains (409), one audit row per changed
    value and `workspace_preset_applied` for a preset, the notices, and the release of
    assignments nobody may keep. Changing only "Afdelingen gebruiken" while everyone sees
    all post moves nobody's sight: an audit row, no notice. Labels and rules are never
    removed; `add_start_set` adds the kind's start set."""
    _validate(proposal)
    with transaction.atomic():
        locked_members = lock_memberships(workspace)
        _require_admin(locked_members, actor)
        locked = Workspace.objects.select_for_update().get(id=workspace.id)
        with sight_change(
            locked,
            narrowing=False,
            confirmed=confirmed,
            preview_token=preview_token,
            actor=actor,
            change=proposal_key(proposal),
        ):
            changed = _apply_proposal(locked, proposal)
            target = AuditTarget(TargetKind.WORKSPACE, locked.id, locked.name)
            for name, value in changed.items():
                record(
                    AuditCode.WORKSPACE_SETTING_CHANGED,
                    actor=actor,
                    target=target,
                    workspace_id=locked.id,
                    values={"setting": name, "value": value},
                )
            if proposal.apply_preset:
                record(
                    AuditCode.WORKSPACE_PRESET_APPLIED,
                    actor=actor,
                    target=target,
                    workspace_id=locked.id,
                    values={"preset": locked.kind},
                )
        if proposal.add_start_set:
            from apps.labels.presets import apply_preset

            with workspace_context(locked.id, actor=SYSTEM):
                apply_preset(locked)
        return locked


# --- reading ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Visibility:
    """What `getVisibility` answers."""

    workspace: Workspace
    sorter_ids: frozenset[UUID]
    sentences: list[str]
    summary: list[str]


def visibility_for(membership: Membership) -> Visibility:
    """The switches, who sorts post, and the sentences for this member: "Wie kan mijn
    post zien?" for everyone, the installation's summary for an admin (P9: the switches
    are stated in the member's own sentences, so they are no secret)."""
    workspace = Workspace.objects.get(id=membership.workspace_id)
    policy = snapshot(workspace)
    facts = policy_facts(workspace, policy, membership.user_id)
    admin = membership.role == Membership.Role.ADMIN
    return Visibility(
        workspace=workspace,
        sorter_ids=policy.sorter_ids,
        sentences=describe(facts, audience="member"),
        summary=describe(facts, audience="summary") if admin else [],
    )


def post_access(membership: Membership) -> list[str]:
    """ "Wie kan mijn post zien?" for this member (S 4.15)."""
    workspace = Workspace.objects.get(id=membership.workspace_id)
    facts = policy_facts(workspace, snapshot(workspace), membership.user_id)
    return describe(facts, audience="member")


def _incomplete() -> ApiError:
    return ApiError(
        422, "validation_error", _("Zeg om welk lid, welke persoon of welke rol het gaat.")
    )


def _member_step(
    workspace: Workspace, change: MemberChange, actor: AuditActor
) -> tuple[Callable[[], object], bool]:
    """The writer's own step for a planned member change, and whether it narrows."""
    from apps.people import services as people
    from apps.workspaces import members

    user_id, person_id = change.user_id, change.person_id
    if change.kind == "role":
        if user_id is None or not change.role:
            raise _incomplete()
        current = (
            Membership.objects.filter(workspace=workspace, user_id=user_id)
            .values_list("role", flat=True)
            .first()
        )
        narrowing = current is not None and Membership.RANK[change.role] < Membership.RANK[current]
        role = change.role
        return (
            lambda: members.change_role(
                workspace=workspace, user_id=user_id, role=role, actor=actor
            ),
            narrowing,
        )
    if change.kind == "sorts_post":
        if user_id is None:
            raise _incomplete()
        sorts = change.sorts_post
        return (
            lambda: members.set_sorts_post(
                workspace=workspace, user_id=user_id, sorts_post=sorts, actor=actor
            ),
            not sorts,
        )
    if change.kind == "remove":
        if user_id is None:
            raise _incomplete()
        return (
            lambda: members.removal_preview_step(workspace=workspace, user_id=user_id, actor=actor),
            True,
        )
    if person_id is None:
        raise _incomplete()
    if change.kind in ("department_add", "department_remove"):
        if user_id is None:
            raise _incomplete()
        if change.kind == "department_add":
            return (
                lambda: people.add_department_member(
                    workspace=workspace, person_id=person_id, user_id=user_id, actor=actor
                ),
                False,
            )
        return (
            lambda: people.remove_department_member(
                workspace=workspace, person_id=person_id, user_id=user_id, actor=actor
            ),
            True,
        )
    if change.kind == "link" and user_id is None:
        raise _incomplete()
    target = user_id if change.kind == "link" else None
    # The caller's own role, read here: never assumed.
    actor_role = (
        Membership.objects.filter(workspace=workspace, user_id=actor.user_id)
        .values_list("role", flat=True)
        .first()
        if actor.user_id is not None
        else None
    )
    if actor_role is None:
        raise _forbidden()
    return (
        lambda: people.link_member(
            workspace=workspace,
            person_id=person_id,
            user_id=target,
            actor=actor,
            actor_role=actor_role,
        ),
        change.kind == "unlink",
    )


def _with_newcomer(policy: PolicySnapshot, role: str) -> PolicySnapshot:
    newcomer = MemberFacts(
        user_id=UUID(int=0),
        name="",
        role=role,
        sorts_post=False,
        person_ids=frozenset(),
        department_ids=frozenset(),
    )
    return PolicySnapshot(
        switches=policy.switches,
        members=(*policy.members, newcomer),
        person_names=policy.person_names,
        department_names=policy.department_names,
    )


def invited_admin_changes(workspace: Workspace) -> tuple[PolicySnapshot, list[Change]]:
    """What inviting an admin changes beyond inviting a member (pure): the state now, and
    the difference between the newcomer joining as a member and as an admin. Nothing in
    Thuis (everyone sees all anyway); with admins reading all post, all post; with nobody
    flagged, the unnamed post as a fallback sorter."""
    before = snapshot(workspace)
    as_member = _with_newcomer(before, Membership.Role.MEMBER)
    as_admin = _with_newcomer(before, Membership.Role.ADMIN)
    return before, diff(as_member, as_admin)


def _preview_invite_admin(workspace: Workspace, actor_id: UUID) -> PreviewResult:
    """The preview of an admin invitation: a placeholder newcomer joins as a member, and
    the preview shows what making them admin changes; the token is over the state
    without the placeholder, as `create_invitation` checks it. All rolled back."""
    real = snapshot(workspace)
    with transaction.atomic():
        user = User.objects.create(email=f"{uuid4()}@preview.invalid", name=_("Nieuwe beheerder"))
        membership = Membership.objects.create(
            user=user, workspace=workspace, role=Membership.Role.MEMBER
        )

        def promote() -> None:
            Membership.objects.filter(id=membership.id).update(role=Membership.Role.ADMIN)

        result = preview(
            workspace,
            promote,
            narrowing=False,
            actor_id=actor_id,
            change=member_change_key(MemberChange(kind="invite_admin")),
            token_state=real,
        )
        transaction.set_rollback(True)
    return result


def preview_request(
    *, workspace: Workspace, proposal: Proposal, member_change: MemberChange, actor: AuditActor
) -> PreviewResult:
    """The preview of a settings change, or of a planned member change (the writer's own
    step, its refusals included). Admins only, by the endpoint."""
    if actor.user_id is None:
        raise _forbidden()
    if not member_change.kind:
        return preview_change(workspace=workspace, proposal=proposal, actor_id=actor.user_id)
    if member_change.kind == "invite_admin":
        return _preview_invite_admin(workspace, actor.user_id)
    step, narrowing = _member_step(workspace, member_change, actor)

    def apply() -> None:
        step()

    return preview(
        workspace,
        apply,
        narrowing=narrowing,
        actor_id=actor.user_id,
        change=member_change_key(member_change),
    )


def in_words(result: PreviewResult) -> list[Change]:
    """The preview's changes with every kind of post in words ("post van Finance")."""
    return [
        Change(
            user_id=c.user_id,
            name=c.name,
            gains=tuple(category_phrase(g, result.after) for g in c.gains),
            loses=tuple(category_phrase(lost, result.after) for lost in c.loses),
        )
        for c in result.changes
    ]
