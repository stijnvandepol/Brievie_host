"""Names people type for labels and persons: they must show something and keep the
direction of the text around them."""

from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.common.text import has_direction_control, is_visible


def check_shown(name: str, *, empty: str) -> None:
    """Refuse a name that shows nothing (`empty` is the sentence for that) or that holds
    a character turning the text around (U+202E and the like)."""
    if not is_visible(name):
        raise ApiError(422, "validation_error", empty)
    if has_direction_control(name):
        raise ApiError(
            422,
            "validation_error",
            _("Een naam mag geen tekens bevatten die de leesrichting van de tekst omdraaien."),
        )
