"""Search answers only with letters the caller may read (S 4.1, through the one seam).

A letter the caller may not see is never found by its text, sender, summary, label or
person, never through the label or person filter, never through the typo fallback, and
it changes neither `next_offset` nor `fuzzy`: the answer is the one the caller would get
if the letter did not exist. Whoever the policy lets read it does find it.
"""

import uuid
from dataclasses import dataclass

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.documents.models import Document, DocumentLabel
from apps.documents.search import SearchFilters, search_documents
from apps.labels.models import Label
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.documents_support import documents_url
from tests.visibility_support import ALL_16_STATES, Office, as_actor, office

# Per letter: a word only in its text, its sender and its summary. No two share a
# beginning or many trigrams, so nothing is found through another letter.
WORDS = {
    "own_piet": ("appelboom", "Jachthaven", "rozenstruik"),
    "finance_letter": ("bergkristal", "Kikkerpoel", "sneeuwpop"),
    "hr_letter": ("citroenpers", "Lantaarnpaal", "tulpenveld"),
    "unassigned": ("dakgoot", "Molenwiek", "uiensoep"),
    "only_directie": ("eikenhout", "Notenkraker", "vuurtoren"),
    "person_without_account": ("fietsbel", "Oliebol", "walvis"),
    "scanned_in_review": ("gieter", "Pannenkoek", "xylofoon"),
    "private_femke": ("hangmat", "Quizmaster", "zeilboot"),
}
COMMON = "kantoorpost"
LABEL = "Zwaluwstaart"
LABELLED = ("own_piet", "finance_letter", "private_femke")


@dataclass(frozen=True)
class Answer:
    names: frozenset[str]
    next_offset: int | None
    fuzzy: bool


NOTHING = Answer(frozenset(), None, False)


def searchable_office() -> tuple[Office, Label]:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        for name, (text, sender, summary) in WORDS.items():
            Document.objects.filter(id=o.letters[name].id).update(
                ocr_text=f"Brief {text} over de {COMMON}.", sender=sender, summary=summary
            )
        label = Label.objects.create(workspace=o.workspace, name=LABEL)
        for name in LABELLED:
            DocumentLabel.objects.create(
                workspace=o.workspace, document=o.letters[name], label=label
            )
    return o, label


def find(
    o: Office, user: User, q: str, *, limit: int = 30, offset: int = 0, **filters: object
) -> Answer:
    member = Membership.objects.get(workspace=o.workspace, user=user)
    with as_actor(o.workspace, user.id):
        result = search_documents(
            member,
            q=q,
            filters=SearchFilters(**filters),  # type: ignore[arg-type]
            limit=limit,
            offset=offset,
        )
    return Answer(
        frozenset(o.name_of(hit.document.id) for hit in result.hits),
        result.next_offset,
        result.fuzzy,
    )


def exactly(names: set[str]) -> Answer:
    return Answer(frozenset(names), None, False)


@pytest.mark.parametrize("state", ALL_16_STATES)
def test_search_finds_exactly_what_the_policy_shows(db: None, state: dict[str, bool]) -> None:
    o, label = searchable_office()
    o.state(**state)
    finance = o.persons["Finance"].id
    for role, user in o.users.items():
        sees = o.visible(user.id)
        why = (state, role)
        assert find(o, user, COMMON) == exactly(sees), why
        # Through a label name and a person name (Femke's letters; Finance's letter).
        assert find(o, user, LABEL) == exactly(sees & set(LABELLED)), why
        assert find(o, user, "femke") == exactly(sees & {"scanned_in_review", "private_femke"}), why
        assert find(o, user, "finance") == exactly(sees & {"finance_letter"}), why
        # The filters narrow; they never add a letter.
        assert find(o, user, COMMON, label_id=label.id) == exactly(sees & set(LABELLED)), why
        assert find(o, user, COMMON, person_id=finance) == exactly(sees & {"finance_letter"}), why
        for name, (_text, sender, _summary) in WORDS.items():
            assert find(o, user, sender) == exactly(sees & {name}), (*why, name)


def test_a_hidden_letter_is_not_found_by_text_sender_or_summary(db: None) -> None:
    """Own post (E off, D on, A off, Sandra sorts): Piet sees his letter and the one he
    scanned; Femke sees hers and Finance's."""
    o, _label = searchable_office()
    for user in (o.plain, o.finance, o.viewer):
        sees = o.visible(user.id)
        for name, words in WORDS.items():
            for word in words:
                expected = exactly({name}) if name in sees else NOTHING
                assert find(o, user, word) == expected, (user.name, name, word)


def test_the_typo_fallback_finds_no_hidden_letter(db: None) -> None:
    o, _label = searchable_office()
    typo = "Kikkerpoal"  # Kikkerpoel, the sender of Finance's letter

    assert "finance_letter" not in o.visible(o.plain.id)
    assert find(o, o.plain, typo) == NOTHING
    assert find(o, o.finance, typo) == Answer(frozenset({"finance_letter"}), None, True)


def test_a_hidden_word_does_not_switch_on_the_fallback_differently(db: None) -> None:
    """`bergkristal` is only in Finance's letter. For Piet the exact step finds nothing,
    as if the letter did not exist: the fallback runs and also finds nothing."""
    o, _label = searchable_office()

    assert find(o, o.plain, "bergkristal") == NOTHING
    assert find(o, o.finance, "bergkristal") == exactly({"finance_letter"})


def test_next_offset_counts_only_letters_the_caller_sees(db: None) -> None:
    o, _label = searchable_office()
    sees = o.visible(o.plain.id)
    assert len(sees) == 2

    first = find(o, o.plain, COMMON, limit=1)
    second = find(o, o.plain, COMMON, limit=1, offset=1)

    assert first.next_offset == 1
    assert second.next_offset is None
    assert first.names | second.names == sees


def test_an_admin_finds_all_post_only_when_admins_read_all(db: None) -> None:
    o, _label = searchable_office()
    o.state(e=False, d=True, a=False)
    assert "finance_letter" not in o.visible(o.admin.id)
    assert find(o, o.admin, "Kikkerpoel") == NOTHING

    o.state(e=False, d=True, a=True)
    assert find(o, o.admin, "Kikkerpoel") == exactly({"finance_letter"})
    # A private letter stays private, also for an admin who reads all.
    assert find(o, o.admin, "Quizmaster") == NOTHING
    assert find(o, o.finance, "Quizmaster") == exactly({"private_femke"})


def test_the_search_endpoint_answers_as_the_signed_in_member(db: None, client: Client) -> None:
    o, label = searchable_office()
    client.force_login(o.plain)
    url = f"{documents_url(o.workspace.id)}/search"

    def names(body: dict[str, object]) -> set[str]:
        response = client.post(url, body, content_type="application/json")
        assert response.status_code == 200
        return {o.name_of(uuid.UUID(item["id"])) for item in response.json()["items"]}

    assert names({"q": COMMON}) == {"own_piet", "scanned_in_review"}
    assert names({"q": "Kikkerpoel"}) == set()
    assert names({"q": COMMON, "label_id": str(label.id)}) == {"own_piet"}
    assert names({"q": COMMON, "person_id": str(o.persons["Finance"].id)}) == set()
    hidden = client.post(url, {"q": "Kikkerpoal"}, content_type="application/json").json()
    assert hidden["items"] == []
    assert hidden["fuzzy"] is False
    assert hidden["next_offset"] is None
