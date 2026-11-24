"""The one hook for a retention term: "Bewaren tot" (`Document.retain_until`, archive spec
S 7.2, Besluit B4).

`check_removal` is asked in exactly four places, on the locked row and before anything
changes: `trash.move_to_trash` (step `trash`), and `trash.purge_now`, `trash.empty_trash`
and `purge.mark_expired` (step `purge`). Until the day has passed (Europe/Amsterdam) a
document may go to the trash, with a warning the clients ask first, but is never purged:
purge-now answers 409 `document_retained`, emptying the trash and the expiry leave it in
the trash. Clearing the date (`updateDocumentDetails`) allows the purge. The sentences
name the date only, never the document's content. Brievie does not know the law: there is
no default per type.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from django.utils import formats, timezone
from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.documents.models import Document

RemovalStep = Literal["trash", "purge"]


@dataclass(frozen=True)
class RemovalVerdict:
    allowed: bool = True
    # A sentence for the user (Dutch); empty when there is nothing to say.
    warning: str = ""


def check_removal(
    document: Document, *, step: RemovalStep, today: date | None = None
) -> RemovalVerdict:
    """Whether the document may go to the trash (`trash`: yes, with a warning while it is
    retained) or be purged (`purge`: not before its "Bewaren tot" has passed)."""
    until = document.retain_until
    if until is None or until < (today or timezone.localdate()):
        return RemovalVerdict()
    day = formats.date_format(until, "j F Y")
    if step == "trash":
        return RemovalVerdict(
            allowed=True, warning=_("Bewaren tot %(date)s. Weet je het zeker?") % {"date": day}
        )
    return RemovalVerdict(
        allowed=False,
        warning=_(
            "Dit document moet bewaard worden tot %(date)s. Haal de datum weg om het nu te wissen."
        )
        % {"date": day},
    )


def retained_error(verdict: RemovalVerdict) -> ApiError:
    """The answer of a request path the verdict refused."""
    return ApiError(
        409,
        "document_retained",
        verdict.warning or _("Deze brief moet nog bewaard worden. Je kunt hem nu niet wissen."),
    )
