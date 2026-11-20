"""Searching letters (apps.documents.search): matching, ranking, filters, fallback, privacy."""

import uuid
from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.common.errors import ApiError
from apps.documents import search as search_module
from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentLabel
from apps.documents.search import SearchFilters, SearchResult, search_documents, search_out
from apps.labels.models import Label
from apps.people.models import Person
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import make_person

# 111222333 passes the elfproef.
VALID_BSN = "111222333"


def membership_of(workspace: Workspace) -> Membership:
    return Membership.objects.get(workspace=workspace, role="admin")


def letter(
    workspace: Workspace,
    text: str = "",
    *,
    sender: str = "",
    summary: str = "",
    status: str = "done",
    document_type: str = "",
    letter_date: date | None = None,
) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            status=status,
            ocr_text=text,
            sender=sender,
            summary=summary,
            document_type=document_type,
            letter_date=letter_date,
        )


def label_on(workspace: Workspace, document: Document, name: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        label = Label.objects.create(workspace=workspace, name=name)
        DocumentLabel.objects.create(workspace=workspace, document=document, label=label)
    return label


def addressee(workspace: Workspace, document: Document, person: Person) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        DocumentAddressee.objects.create(workspace=workspace, document=document, person=person)
        # Links made by hand: `addressee_ids` follows them as the services keep it.
        visibility.sync_addressee_ids(document)


def search(
    workspace: Workspace,
    q: str,
    filters: SearchFilters | None = None,
    *,
    limit: int = 30,
    offset: int = 0,
) -> SearchResult:
    membership = membership_of(workspace)
    with workspace_context(workspace.id, actor=membership.user_id):
        return search_documents(
            membership,
            q=q,
            filters=filters or SearchFilters(),
            limit=limit,
            offset=offset,
        )


def ids(result: SearchResult) -> list[uuid.UUID]:
    return [hit.document.id for hit in result.hits]


# --- matching ---------------------------------------------------------------------------


def test_a_word_in_the_text_finds_the_letter(ws_a: Workspace) -> None:
    found = letter(ws_a, "Uw aanslag motorrijtuigenbelasting voor 2026.")
    letter(ws_a, "Een uitnodiging voor de tandarts.")

    result = search(ws_a, "motorrijtuigenbelasting")

    assert ids(result) == [found.id]
    assert result.fuzzy is False
    assert result.bsn_removed is False


def test_a_dutch_stem_finds_other_forms(ws_a: Workspace) -> None:
    found = letter(ws_a, "Wij vragen u het bedrag te betalen binnen dertig dagen.")

    result = search(ws_a, "betaling")

    assert ids(result) == [found.id]
    assert result.fuzzy is False


def test_a_word_beginning_finds_the_whole_word(ws_a: Workspace) -> None:
    found = letter(ws_a, "Bericht van de Belastingdienst over uw toeslag.")

    result = search(ws_a, "belastingd")

    assert ids(result) == [found.id]
    assert result.fuzzy is False


def test_every_word_must_match(ws_a: Workspace) -> None:
    both = letter(ws_a, "Zorgverzekering polis 2027 en premie.")
    letter(ws_a, "Zorgverzekering: uw nieuwe pas.")
    letter(ws_a, "De premie van uw autoverzekering.")

    assert ids(search(ws_a, "zorgverzekering premie")) == [both.id]


def test_a_hit_in_the_sender_outranks_one_in_the_text(ws_a: Workspace) -> None:
    in_text = letter(ws_a, "Wij sturen dit namens Gemeente Utrecht.", sender="Waternet")
    in_sender = letter(ws_a, "Uw afvalstoffenheffing.", sender="Gemeente Utrecht")

    assert ids(search(ws_a, "utrecht")) == [in_sender.id, in_text.id]


def test_a_label_name_finds_its_letters(ws_a: Workspace) -> None:
    labelled = letter(ws_a, "Iets heel anders.")
    letter(ws_a, "Nog iets anders.")
    label_on(ws_a, labelled, "Huurwoning")

    assert ids(search(ws_a, "huurwon")) == [labelled.id]


def test_a_person_name_and_a_name_variant_find_their_letters(ws_a: Workspace) -> None:
    for_jan = letter(ws_a, "Een brief.")
    letter(ws_a, "Een andere brief.")
    jan = make_person(ws_a, "Jan de Vries")
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.filter(id=jan.id).update(name_variants=["J. Devriesje"])
    addressee(ws_a, for_jan, jan)

    assert ids(search(ws_a, "vries")) == [for_jan.id]
    assert ids(search(ws_a, "devriesje")) == [for_jan.id]


def test_an_archived_persons_name_still_finds_their_letters(ws_a: Workspace) -> None:
    document = letter(ws_a, "Een brief.")
    oma = make_person(ws_a, "Oma Bep", archived=True)
    addressee(ws_a, document, oma)

    assert ids(search(ws_a, "bep")) == [document.id]


def test_words_combine_across_text_and_a_person(ws_a: Workspace) -> None:
    for_papa = letter(ws_a, "Uw aanslag inkomstenbelasting 2026.")
    letter(ws_a, "Uw aanslag waterschapsbelasting.")
    letter(ws_a, "Een brief voor Papa.")
    other_for_papa = letter(ws_a, "Een uitnodiging.")
    papa = make_person(ws_a, "Papa")
    addressee(ws_a, for_papa, papa)
    addressee(ws_a, other_for_papa, papa)

    assert ids(search(ws_a, "aanslag papa")) == [for_papa.id]
    assert ids(search(ws_a, "papa aanslag")) == [for_papa.id]
    # A stop word is not required, also when it is part of a name ("de" in a preset label).
    assert ids(search(ws_a, "de aanslag voor papa")) == [for_papa.id]


def test_words_combine_across_a_label_and_the_text(ws_a: Workspace) -> None:
    labelled = letter(ws_a, "De jaarafrekening van uw energie.")
    letter(ws_a, "De jaarafrekening van uw water.")
    other = letter(ws_a, "Een herinnering.")
    label = label_on(ws_a, labelled, "Belasting")
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentLabel.objects.create(workspace=ws_a, document=other, label=label)

    assert ids(search(ws_a, "belasting jaarafrekening")) == [labelled.id]
    assert ids(search(ws_a, "jaarafrekening belasting")) == [labelled.id]


def test_all_words_in_names(ws_a: Workspace) -> None:
    both = letter(ws_a, "Een brief.")
    only_label = letter(ws_a, "Een andere brief.")
    label = label_on(ws_a, both, "Huurwoning")
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentLabel.objects.create(workspace=ws_a, document=only_label, label=label)
    papa = make_person(ws_a, "Papa")
    addressee(ws_a, both, papa)

    assert ids(search(ws_a, "papa huurwon")) == [both.id]
    assert ids(search(ws_a, "huurwon papa")) == [both.id]


def test_all_words_in_the_text_rank_more_words_higher(ws_a: Workspace) -> None:
    both = letter(ws_a, "De parkeervergunning en de parkeerboete.")
    letter(ws_a, "Alleen een parkeervergunning.")

    assert ids(search(ws_a, "parkeervergunning parkeerboete")) == [both.id]


def test_a_word_in_neither_text_nor_name_finds_nothing(ws_a: Workspace) -> None:
    document = letter(ws_a, "Uw aanslag inkomstenbelasting.")
    addressee(ws_a, document, make_person(ws_a, "Papa"))

    result = search(ws_a, "aanslag papa qqqzzz")

    assert result.hits == []


def test_a_letter_matching_a_word_in_text_and_name_ranks_above_text_only(
    ws_a: Workspace,
) -> None:
    text_only = letter(ws_a, "Uw huur en de huurwoning.")
    named = letter(ws_a, "Uw huur.")
    label_on(ws_a, named, "Huurwoning")

    found = ids(search(ws_a, "huurwoning"))
    assert found == [named.id, text_only.id]


# --- filters ----------------------------------------------------------------------------


def test_document_type_filter(ws_a: Workspace) -> None:
    invoice = letter(ws_a, "Huur december", document_type="invoice")
    letter(ws_a, "Huur november", document_type="reminder")

    assert ids(search(ws_a, "huur", SearchFilters(document_type="invoice"))) == [invoice.id]


def test_label_filter(ws_a: Workspace) -> None:
    labelled = letter(ws_a, "Huur december")
    letter(ws_a, "Huur november")
    label = label_on(ws_a, labelled, "Appartement")

    assert ids(search(ws_a, "huur", SearchFilters(label_id=label.id))) == [labelled.id]


def test_person_filter(ws_a: Workspace) -> None:
    for_papa = letter(ws_a, "Huur december")
    letter(ws_a, "Huur november")
    papa = make_person(ws_a, "Papa")
    addressee(ws_a, for_papa, papa)

    assert ids(search(ws_a, "huur", SearchFilters(person_id=papa.id))) == [for_papa.id]


def test_inbox_state_filter(ws_a: Workspace) -> None:
    review = letter(ws_a, "Huur december", status="needs_review")
    letter(ws_a, "Huur november", status="done")

    assert ids(search(ws_a, "huur", SearchFilters(inbox_state="review"))) == [review.id]


def test_date_filters_use_the_letter_date(ws_a: Workspace) -> None:
    early = letter(ws_a, "Huur januari", letter_date=date(2026, 1, 15))
    late = letter(ws_a, "Huur oktober", letter_date=date(2026, 10, 15))

    after = search(ws_a, "huur", SearchFilters(date_from=date(2026, 6, 1)))
    before = search(ws_a, "huur", SearchFilters(date_to=date(2026, 6, 1)))

    assert ids(after) == [late.id]
    assert ids(before) == [early.id]


def test_a_letter_without_a_date_falls_under_its_upload_date(ws_a: Workspace) -> None:
    undated = letter(ws_a, "Huur zonder datum")

    today = undated.created_at.date()
    assert ids(search(ws_a, "huur", SearchFilters(date_from=today))) == [undated.id]
    assert ids(search(ws_a, "huur", SearchFilters(date_to=date(2000, 1, 1)))) == []


def test_filters_together_all_apply(ws_a: Workspace) -> None:
    match = letter(ws_a, "Huur", document_type="invoice", letter_date=date(2026, 3, 1))
    letter(ws_a, "Huur", document_type="invoice", letter_date=date(2025, 3, 1))
    letter(ws_a, "Huur", document_type="reminder", letter_date=date(2026, 3, 1))

    filters = SearchFilters(document_type="invoice", date_from=date(2026, 1, 1))
    assert ids(search(ws_a, "huur", filters)) == [match.id]


@pytest.mark.parametrize(
    "filters",
    [
        SearchFilters(inbox_state="sorted"),
        SearchFilters(document_type="brief"),
        SearchFilters(date_from=date(2026, 2, 1), date_to=date(2026, 1, 1)),
    ],
)
def test_unknown_filters_are_refused_without_the_term(
    ws_a: Workspace, filters: SearchFilters
) -> None:
    with pytest.raises(ApiError) as caught:
        search(ws_a, "geheimwoord", filters)

    assert caught.value.status == 422
    assert caught.value.code == "validation_error"
    assert "geheimwoord" not in caught.value.message


# --- paging -----------------------------------------------------------------------------


def test_paging_gives_the_next_offset_until_the_end(ws_a: Workspace) -> None:
    letters = [letter(ws_a, f"Huur maand {n}") for n in range(5)]

    first = search(ws_a, "huur", limit=2)
    second = search(ws_a, "huur", limit=2, offset=2)
    last = search(ws_a, "huur", limit=2, offset=4)

    assert first.next_offset == 2
    assert second.next_offset == 4
    assert last.next_offset is None
    found = ids(first) + ids(second) + ids(last)
    assert sorted(found) == sorted(d.id for d in letters)
    # Equal rank: newest first.
    assert found == [d.id for d in reversed(letters)]


# --- the typo fallback ------------------------------------------------------------------


def test_a_typo_finds_the_letter_as_an_approximate_match(ws_a: Workspace) -> None:
    found = letter(ws_a, "Brief van de belastingdienst.", sender="Belastingdienst")
    letter(ws_a, "Een uitnodiging voor de tandarts.")

    result = search(ws_a, "belastingdeinst")

    assert ids(result) == [found.id]
    assert result.fuzzy is True


def test_no_fallback_when_an_exact_hit_exists(ws_a: Workspace) -> None:
    exact = letter(ws_a, "De belastingdienst schrijft.")
    letter(ws_a, "De belastingdeinst schrijft met een typfout.")

    result = search(ws_a, "belastingdienst")

    assert ids(result) == [exact.id]
    assert result.fuzzy is False


def test_nothing_similar_finds_nothing(ws_a: Workspace) -> None:
    letter(ws_a, "Een uitnodiging voor de tandarts.")

    result = search(ws_a, "kwxzqv")

    assert result.hits == []
    assert result.next_offset is None


# --- the term ---------------------------------------------------------------------------


def test_a_bsn_in_the_term_is_removed(ws_a: Workspace) -> None:
    letter(ws_a, "Uw BSN is [BSN].")

    result = search(ws_a, VALID_BSN)

    assert result.bsn_removed is True
    assert result.hits == []


def test_a_bsn_with_the_word_bsn_is_removed_and_the_rest_searched(ws_a: Workspace) -> None:
    # Only "zorgtoeslag" is searched: also the letter without a (masked) BSN is found.
    with_bsn = letter(ws_a, "Uw zorgtoeslag. BSN: [BSN]")
    without = letter(ws_a, "Uw zorgtoeslag voor 2027.")
    letter(ws_a, "Een andere brief. BSN: [BSN]")

    result = search(ws_a, f"zorgtoeslag BSN {VALID_BSN}")

    assert result.bsn_removed is True
    assert set(ids(result)) == {with_bsn.id, without.id}


@pytest.mark.parametrize("q", ["bsn", "BSN", "[bsn", "bsn]", "[ BSN ]", "Burgerservicenummer"])
def test_the_word_bsn_finds_nothing_and_says_so(ws_a: Workspace, q: str) -> None:
    letter(ws_a, "Uw BSN is [BSN].")
    letter(ws_a, "Het burgerservicenummer staat niet op deze brief.")

    result = search(ws_a, q)

    assert result.hits == []
    assert result.bsn_removed is True


@pytest.mark.parametrize(
    "q",
    [
        "111\u2013222\u2013333",  # en dash
        "111/222/333",
        "111,222,333",
        "111_222_333",
        "111\u200b222333",  # zero-width space
        "111\u00ad222\u00ad333",  # soft hyphen
        "\uff11\uff11\uff11222333",  # full-width digits (NFKC)
        "1112/22/333",
        "zorgtoeslag 111;222;333",
    ],
)
def test_a_bsn_with_other_separators_is_removed(ws_a: Workspace, q: str) -> None:
    # Stored text never holds a BSN; this letter stands for a number the mask missed.
    letter(ws_a, "Kenmerk 111222333 en 111 222 333.")

    result = search(ws_a, q)

    assert result.bsn_removed is True
    assert result.hits == []


def test_nine_digits_that_fail_the_elfproef_are_kept(ws_a: Workspace) -> None:
    # 123456789 fails the elfproef: a reference number, not a BSN.
    found = letter(ws_a, "Uw kenmerk 123 456 789 bij de gemeente.")

    result = search(ws_a, "123/456/789")

    assert result.bsn_removed is False
    assert ids(result) == [found.id]


def test_the_literal_mask_finds_nothing(ws_a: Workspace) -> None:
    letter(ws_a, "Uw nummer is [BSN] en verder niets.")

    result = search(ws_a, "[BSN]")

    assert result.hits == []
    assert result.bsn_removed is False


def test_a_term_of_one_character_finds_nothing(ws_a: Workspace) -> None:
    letter(ws_a, "a b c d e")

    assert search(ws_a, "a").hits == []
    assert search(ws_a, "   ").hits == []


@pytest.mark.parametrize(
    "q",
    [
        "a & !b | (c:*",
        "huur & !",
        "'huur'",
        '"huur" \'',
        "huur\\",
        "\\\\ \\' ''",
        "!!! ::: *** &&& ||| ((( )))",
        "<-> <2> huur:*A",
        "o'brien",
        "-",
        "e-mail. ...",
        "huur " * 50,
    ],
)
def test_tsquery_syntax_in_the_term_is_harmless(ws_a: Workspace, q: str) -> None:
    letter(ws_a, "Huur december.")

    result = search(ws_a, q[:200])

    assert isinstance(result.hits, list)


def test_only_eight_words_are_used(ws_a: Workspace) -> None:
    found = letter(ws_a, "een twee drie vier vijf zes zeven acht")

    # The ninth word is not in the letter, but it is not used either.
    assert ids(search(ws_a, "een twee drie vier vijf zes zeven acht negen")) == [found.id]


# --- the fragment -----------------------------------------------------------------------


def test_the_fragment_marks_the_hit_without_markup(ws_a: Workspace) -> None:
    letter(
        ws_a,
        "Geachte heer, hierbij ontvangt u de aanslag voor de waterschapsbelasting "
        "van dit jaar. Betaal het bedrag binnen dertig dagen.",
    )

    (hit,) = search(ws_a, "waterschapsbelasting").hits

    marked = [part.text for part in hit.snippet if part.hit]
    assert marked == ["waterschapsbelasting"]
    text = "".join(part.text for part in hit.snippet)
    assert "\x02" not in text
    assert "\x03" not in text
    assert len(text) <= 240
    # Adjacent parts are merged: hits and non-hits alternate.
    assert all(a.hit != b.hit for a, b in zip(hit.snippet, hit.snippet[1:], strict=False))


def test_a_hit_outside_the_text_shows_the_start_of_the_summary(ws_a: Workspace) -> None:
    letter(
        ws_a,
        "Inhoud zonder het woord.",
        sender="Vattenfall",
        summary="Vattenfall stuurt de jaarafrekening voor stroom.",
    )

    (hit,) = search(ws_a, "vattenf").hits

    assert [(p.text, p.hit) for p in hit.snippet] == [
        ("Vattenfall", True),
        (" stuurt de jaarafrekening voor stroom.", False),
    ]


def test_the_fallback_fragment_marks_similar_words(ws_a: Workspace) -> None:
    letter(ws_a, "Uw aanslag.", summary="Brief van de belastingdienst over uw aanslag.")

    result = search(ws_a, "belastingdeinst")

    (hit,) = result.hits
    assert [p.text for p in hit.snippet if p.hit] == ["belastingdienst"]


def test_a_typo_in_a_word_that_is_only_in_the_text_finds_nothing(ws_a: Workspace) -> None:
    # Measured: word similarity over whole letters is too slow (DECISIONS 2026-11-20).
    letter(ws_a, "Brief van de belastingdienst over uw aanslag.")

    result = search(ws_a, "belastingdeinst")

    assert result.hits == []
    assert result.fuzzy is False


def test_a_very_common_word_ranks_only_the_newest_candidates(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_module, "MAX_CANDIDATES", 2)
    old = letter(ws_a, "rekening", sender="Rekening BV")
    newer = [letter(ws_a, "een rekening") for _ in range(2)]

    hits = search(ws_a, "rekening").hits

    # The old letter would rank first (sender), but it is outside the newest two.
    assert {hit.document.id for hit in hits} == {d.id for d in newer}
    assert old.id not in {hit.document.id for hit in hits}


def test_a_long_text_gives_a_short_fragment(ws_a: Workspace) -> None:
    letter(ws_a, "woord " * 2000 + "parkeervergunning " + "woord " * 2000)

    (hit,) = search(ws_a, "parkeervergunning").hits

    assert len("".join(part.text for part in hit.snippet)) <= 240
    assert any(part.hit for part in hit.snippet)


# --- isolation and cost -----------------------------------------------------------------


def test_a_letter_of_another_workspace_is_never_found(ws_a: Workspace, ws_b: Workspace) -> None:
    letter(ws_b, "Geheime brief over de fusie.", sender="Fusiepartner")

    assert search(ws_a, "fusie").hits == []
    assert search(ws_a, "fusiepartnr").hits == []  # the fallback neither


def test_without_workspace_context_nothing_is_found(ws_a: Workspace) -> None:
    """Without a context there is no actor: the seam refuses (fails closed) rather than
    answer for nobody. Forced RLS would also hide every letter from the test role."""
    letter(ws_a, "Geheime brief over de fusie.")

    with pytest.raises(RuntimeError):
        search_documents(membership_of(ws_a), q="fusie", filters=SearchFilters(), limit=30, offset=0)


@pytest.fixture
def many_letters(ws_a: Workspace) -> None:
    papa = make_person(ws_a, "Papa")
    for n in range(12):
        document = letter(ws_a, f"Huur maand {n} voor het appartement.")
        addressee(ws_a, document, papa)
        label_on(ws_a, document, f"Label {n}")


@pytest.mark.usefixtures("many_letters")
def test_the_number_of_queries_does_not_grow_with_the_page(ws_a: Workspace) -> None:
    def count(limit: int) -> int:
        membership = membership_of(ws_a)
        with (
            workspace_context(ws_a.id, actor=membership.user_id),
            CaptureQueriesContext(connection) as queries,
        ):
            result = search_documents(
                membership, q="huur", filters=SearchFilters(), limit=limit, offset=0
            )
            answer = search_out(result)
        assert len(answer.items) == limit
        return len(queries)

    assert count(2) == count(12)
