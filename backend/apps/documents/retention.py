"""The one hook for a retention term (spec section 8, `bewaren_tot`; later).

`check_removal` is asked in exactly four places, on the locked row and before anything
changes: `trash.move_to_trash` (step `trash`), and `trash.purge_now`, `trash.empty_trash`
and `purge.mark_expired` (step `purge`). Brievie does not watch a retention term yet, so
today it allows every letter. A refusal answers 409 `document_retained` in the request
paths and leaves the letter where it is in the others.
"""

from dataclasses import dataclass
from typing import Literal

from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.documents.models import Document

RemovalStep = Literal["trash", "purge"]


@dataclass(frozen=True)
class RemovalVerdict:
    allowed: bool = True
    # A sentence for the user (Dutch); empty when there is nothing to say.
    warning: str = ""


def check_removal(document: Document, *, step: RemovalStep) -> RemovalVerdict:
    """Whether the letter may go to the trash (`trash`) or be purged (`purge`)."""
    return RemovalVerdict()


def retained_error(verdict: RemovalVerdict) -> ApiError:
    """The answer of a request path the verdict refused."""
    return ApiError(
        409,
        "document_retained",
        verdict.warning or _("Deze brief moet nog bewaard worden. Je kunt hem nu niet wissen."),
    )
