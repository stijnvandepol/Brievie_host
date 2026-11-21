"""Notices carry no letter content (S 4.10, 4.14, plan Task 18).

A notice is kept per account and shown on every device, also after the member lost
sight of a letter. Its values therefore come from a fixed list per code that holds no
sender, summary, label or text: a letter is named by its id only.
"""

import re

import pytest

from apps.accounts.models import AccountNotice
from apps.accounts.notices import MESSAGES, NOTICE_VALUE_KEYS, notify
from tests.factories import UserFactory

ALLOWED = frozenset(
    {
        "workspace",
        "person",
        "department",
        "category",
        "count",
        "document_id",
        "member",
        "setting",
        "device",
    }
)
CONTENT = ("sender", "summary", "label", "text", "description", "ocr_text", "quote", "title")
PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def test_every_notice_code_has_its_value_keys() -> None:
    assert set(NOTICE_VALUE_KEYS) == set(AccountNotice.Code.values)
    assert set(MESSAGES) == set(AccountNotice.Code.values)


def test_no_value_key_can_hold_content() -> None:
    union = frozenset().union(*NOTICE_VALUE_KEYS.values())
    assert union <= ALLOWED
    for key in CONTENT:
        assert key not in union


def test_notify_refuses_an_unknown_key() -> None:
    user = UserFactory.create()
    with pytest.raises(ValueError, match="sender"):
        notify(
            user,
            AccountNotice.Code.PRIVATE_WITHOUT_READERS,
            actor_name="",
            values={"workspace": "Kantoor", "sender": "Belastingdienst"},
        )
    with pytest.raises(ValueError, match="device"):
        notify(user, AccountNotice.Code.SESSIONS_ENDED, actor_name="Anne", values={"device": "x"})
    assert not AccountNotice.objects.filter(user=user).exists()

    notify(user, AccountNotice.Code.DEVICE_PAIRED, actor_name="Anne", values={"device": "iPhone"})
    assert AccountNotice.objects.filter(user=user).count() == 1


@pytest.mark.parametrize("code", sorted(AccountNotice.Code.values))
def test_every_message_formats_with_only_its_keys(code: str) -> None:
    keys = NOTICE_VALUE_KEYS[code]
    assert set(PLACEHOLDER.findall(str(MESSAGES[code]))) <= {*keys, "name", "date"}
    sample = {key: f"<{key}>" for key in keys} | {"name": "Anne", "date": "3 mei"}
    text = str(MESSAGES[code]) % sample  # a plain dict: a missing key is a KeyError
    assert "%(" not in text
