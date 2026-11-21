"""The sentences that say who sees what (spec S 4.2, 4.3, 4.15).

Pure: a `PolicyFacts` in, Dutch sentences out (through gettext, so English follows the
reader's language). Two audiences: `summary` heads "Zo is Brievie nu ingesteld" and the
"Wie ziet wat" page (what the installation does, for an admin), `member` is "Wie kan mijn
post zien?" (who can see the viewer's own post). A Thuis workspace says "groep" where an
Organisatie says "afdeling" (R51).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from django.utils.translation import gettext as _

from apps.workspaces.visibility_rules import (
    ADMIN,
    ALL,
    ALL_AS_ADMIN,
    DEPARTMENT,
    PERSON,
    UNASSIGNED,
    PolicySnapshot,
    split_category,
)

HOME = "home"


@dataclass(frozen=True)
class PolicyFacts:
    snapshot: PolicySnapshot
    # "home" (Thuis) or "business" (Organisatie).
    kind: str
    # Whom the sentences address ("jij"); None for nobody in particular.
    viewer_id: UUID | None
    care_is_private: bool
    emergency_access: bool
    # The external AI service's name; "" when no letter goes to an outside service.
    ai_supplier_name: str
    has_backup: bool
    server_admin_names: tuple[str, ...]
    # AI on the own network (no outside service).
    ai_local: bool = False


def join_names(names: Iterable[str]) -> str:
    """ "Erik", "Erik en Sanne", "Anne, Bob en Carla" (sorted without regard to case)."""
    ordered = sorted(names, key=str.casefold)
    if not ordered:
        return ""
    if len(ordered) == 1:
        return ordered[0]
    return _("%(first)s en %(last)s") % {"first": ", ".join(ordered[:-1]), "last": ordered[-1]}


def category_phrase(category: str, snapshot: PolicySnapshot) -> str:
    """A kind of post in words: "post van Finance", "post zonder naam", ..."""
    kind, item = split_category(category)
    if kind == ALL:
        return _("alle post die niet privé is")
    if kind == ALL_AS_ADMIN:
        return _("alle post als beheerder")
    if kind == UNASSIGNED:
        return _("post zonder naam")
    if kind == DEPARTMENT and item is not None:
        return _("post van %(department)s") % {
            "department": snapshot.department_names.get(item, "")
        }
    if kind == PERSON and item is not None:
        return _("post van %(person)s") % {"person": snapshot.person_names.get(item, "")}
    return category


def _admin_names(snapshot: PolicySnapshot) -> list[str]:
    return [m.name for m in snapshot.members if m.role == ADMIN]


def _flagged_names(snapshot: PolicySnapshot) -> list[str]:
    return [m.name for m in snapshot.members if m.sorts_post]


def _ai_line(facts: PolicyFacts) -> str:
    if facts.ai_supplier_name:
        return _("Tekst van brieven gaat naar %(supplier)s.") % {"supplier": facts.ai_supplier_name}
    if facts.ai_local:
        return _("Brieven worden gelezen door AI op je eigen netwerk.")
    return _("Brieven worden niet door AI gelezen.")


def _summary(facts: PolicyFacts) -> list[str]:
    snapshot = facts.snapshot
    switches = snapshot.switches
    home = facts.kind == HOME
    viewer = snapshot.member(facts.viewer_id) if facts.viewer_id is not None else None
    viewer_is_admin = viewer is not None and viewer.role == ADMIN
    lines: list[str] = []
    if switches.everyone_sees_all:
        lines.append(_("Iedereen in Brievie ziet alle post, behalve post die privé is."))
    elif switches.departments_enabled:
        lines.append(
            _("Ieder ziet zijn eigen post en die van zijn groepen.")
            if home
            else _("Ieder ziet zijn eigen post en die van zijn afdelingen.")
        )
    else:
        lines.append(_("Ieder ziet zijn eigen post."))
    if switches.departments_enabled and not snapshot.department_names:
        lines.append(_("Er zijn nog geen groepen.") if home else _("Er zijn nog geen afdelingen."))
    if not switches.everyone_sees_all:
        flagged = _flagged_names(snapshot)
        admins = _admin_names(snapshot)
        if flagged:
            lines.append(
                _("Post zonder naam zien: %(names)s (verdeelt post).")
                % {"names": join_names(flagged)}
            )
        elif viewer_is_admin and len(admins) == 1:
            lines.append(_("Post zonder naam zie jij, tot je iemand aanwijst die post verdeelt."))
        else:
            lines.append(
                _("Post zonder naam zien de beheerders: %(names)s.") % {"names": join_names(admins)}
            )
        lines.append(
            _("Beheerders lezen alle post die niet privé is.")
            if switches.admins_read_all
            else _("Beheerders lezen geen post die niet voor hen is.")
        )
    lines += _managers(snapshot)
    if facts.care_is_private:
        lines.append(_("Zorgpost is privé."))
    if switches.everyone_sees_all and viewer_is_admin:
        lines.append(_("Je bent beheerder."))
    if facts.emergency_access:
        lines.append(_("Noodtoegang staat aan."))
    lines.append(_ai_line(facts))
    if not facts.has_backup:
        lines.append(_("Er is nog geen back-up."))
    return lines


def _managers(snapshot: PolicySnapshot) -> list[str]:
    """ "Anne beheert de post van Oma." for every member who manages somebody's post."""
    return [
        _("%(member)s beheert de post van %(persons)s.")
        % {
            "member": member.name,
            "persons": join_names(snapshot.person_names.get(p, "") for p in member.managed_ids),
        }
        for member in sorted(snapshot.members, key=lambda m: m.name.casefold())
        if member.managed_ids
    ]


def _member(facts: PolicyFacts) -> list[str]:
    snapshot = facts.snapshot
    switches = snapshot.switches
    home = facts.kind == HOME
    lines: list[str] = []
    if switches.everyone_sees_all:
        lines.append(_("Iedereen in Brievie ziet alle post, behalve privé-post."))
    elif switches.departments_enabled:
        lines.append(
            _(
                "Je post zien: jij en de anderen op wier naam de brief ook staat, de mensen "
                "van een groep als hij op naam van die groep staat, wie de brief scant (tot "
                "hij klaar is) en met wie hij gedeeld is."
            )
            if home
            else _(
                "Je post zien: jij en de anderen op wier naam de brief ook staat, de mensen "
                "van een afdeling als hij op naam van die afdeling staat, wie de brief scant "
                "(tot hij klaar is) en met wie hij gedeeld is."
            )
        )
    else:
        lines.append(
            _(
                "Je post zien: jij en de anderen op wier naam de brief ook staat, wie de "
                "brief scant (tot hij klaar is) en met wie hij gedeeld is."
            )
        )
    if not switches.everyone_sees_all:
        admins = _admin_names(snapshot)
        if switches.admins_read_all:
            lines.append(
                _("De beheerders %(names)s lezen alle post die niet privé is.")
                % {"names": join_names(admins)}
            )
        else:
            lines.append(_("De beheerders lezen je post niet."))
        flagged = _flagged_names(snapshot)
        if flagged:
            lines.append(
                _("Post zonder naam zien: %(names)s (verdeelt post).")
                % {"names": join_names(flagged)}
            )
        else:
            lines.append(
                _("Post zonder naam zien de beheerders: %(names)s.") % {"names": join_names(admins)}
            )
    if any(member.managed_ids for member in snapshot.members):
        lines.append(
            _(
                "Staat een brief ook op naam van iemand zonder account wiens post iemand "
                "beheert, dan ziet wie die post beheert hem ook."
            )
        )
    # Whatever the switches say: a private letter's readers (documents.visibility).
    lines.append(
        _(
            "Privé-post zien alleen: de mensen op wier naam hij staat, de mensen van een "
            "groep op wier naam hij staat, wie de brief scande of privé maakte, en met wie "
            "hij gedeeld is."
        )
        if home
        else _(
            "Privé-post zien alleen: de mensen op wier naam hij staat, de mensen van een "
            "afdeling op wier naam hij staat, wie de brief scande of privé maakte, en met "
            "wie hij gedeeld is."
        )
    )
    if facts.emergency_access:
        lines.append(
            _(
                "Noodtoegang staat aan. De beheerders kunnen je post openen. Ze moeten een "
                "reden geven, en jij krijgt dan een melding."
            )
        )
    else:
        lines.append(_("Noodtoegang staat uit."))
    if facts.server_admin_names:
        lines.append(
            _("Wie de server zelf beheert, kan technisch bij alle gegevens. Dat is %(names)s.")
            % {"names": join_names(facts.server_admin_names)}
        )
    lines.append(_ai_line(facts))
    return lines


def describe(facts: PolicyFacts, *, audience: Literal["summary", "member"]) -> list[str]:
    """The sentences for `audience` (see the module's text)."""
    return _summary(facts) if audience == "summary" else _member(facts)
