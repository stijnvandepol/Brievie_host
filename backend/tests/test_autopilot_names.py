"""Whose name an AI pick's quote holds, for applying it on its own (no database).

The autopilot obeys the same rules as name matching: a name masked to "[BSN]" names
nobody, and a quote that names another active person as well (or longer) is no proof.
"""

from apps.people.models import Person
from apps.pipeline.autopilot import named_by_another, quote_names


def person(name: str, *variants: str) -> Person:
    return Person(display_name=name, name_variants=list(variants))


def test_a_masked_name_names_nobody() -> None:
    # Before BSNs were refused, a variant typed as a BSN was stored as "[BSN]"; the AI's
    # quote of a letter with any other BSN holds the same token.
    assert not quote_names("Burgerservicenummer: [BSN]", person("BSN-test", "[BSN]"))
    assert not quote_names("Jan [BSN]", person("Jan [BSN]"))
    assert quote_names("T.a.v. Jan de Vries", person("Jan de Vries"))


def test_a_quote_that_names_another_person_too_is_no_proof() -> None:
    stijn = person("Stijn van de Pol")
    andere = person("Andere Stijn", "Stijn van de Pol")
    quote = "De heer Stijn van de Pol"
    assert quote_names(quote, andere)
    assert named_by_another(quote, andere, [stijn, andere])
    assert named_by_another(quote, stijn, [stijn, andere])


def test_a_longer_name_of_this_person_wins_over_a_shorter_one_of_another() -> None:
    kort, lang = person("Test Persoon"), person("Test Persoon B")
    quote = "T.a.v. Test Persoon B"
    assert not named_by_another(quote, lang, [kort, lang])
    assert named_by_another(quote, kort, [kort, lang])
    assert not named_by_another("T.a.v. Jan", person("Jan"), [person("Piet")])
