"""`viewers()` answers "who sees this letter" with the policy's own condition (S 4.9.1)."""

import pytest

from apps.documents.visibility import viewers
from apps.workspaces.context import SYSTEM
from tests.visibility_support import ALL_16_STATES, Office, as_actor, office


@pytest.mark.parametrize("state", ALL_16_STATES)
def test_viewers_equal_the_policy_for_every_actor_and_letter(state: dict[str, bool]) -> None:
    o: Office = office()
    o.state(**state)
    seen_by = {name: o.visible(user.id) for name, user in o.users.items()}

    with as_actor(o.workspace, SYSTEM):
        answer = viewers([d.id for d in o.letters.values()])

    for letter, document in o.letters.items():
        expected = {u.id for name, u in o.users.items() if letter in seen_by[name]}
        assert answer[document.id] == expected, (state, letter)


def test_viewers_of_nothing_is_empty() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        assert viewers([]) == {}
