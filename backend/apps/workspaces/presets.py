"""Thuis and Organisatie: the values each kind starts with (spec S 4.2).

A preset is a starting point, not a mode (R41): `create_workspace` applies the kind's
values once, and a later switch of the kind offers the other values through
`visibility_settings.change` (`apply_preset`), which audits every value it changes.
"""

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class PresetValues:
    everyone_sees_all: bool
    departments_enabled: bool
    admins_read_all: bool
    care_is_private: bool
    emergency_access: bool
    allow_member_export: bool


PRESETS: Final[dict[str, PresetValues]] = {
    "home": PresetValues(
        everyone_sees_all=True,
        departments_enabled=False,
        admins_read_all=True,
        care_is_private=True,
        emergency_access=False,
        allow_member_export=True,
    ),
    "business": PresetValues(
        everyone_sees_all=False,
        departments_enabled=True,
        admins_read_all=False,
        care_is_private=False,
        emergency_access=True,
        allow_member_export=False,
    ),
}

# What a workspace admin's preset switch may change: emergency access is the server
# admin's (S 4.2), so it is never part of it.
ADMIN_PRESET_FIELDS: Final = (
    "everyone_sees_all",
    "departments_enabled",
    "admins_read_all",
    "care_is_private",
    "allow_member_export",
)
