"""The corpus of the duplicate measurement (spec "How the thresholds are measured").

Plain Python, seeded: generated Dutch letters of 40 senders on six real-world templates
(tax assessment, health insurer monthly invoice, energy annual statement, municipality,
bank, school), 1 to 4 pages; the same letter "scanned again" through an OCR noise model;
and the hard negatives (a reminder, a second instalment, next month's invoice, the same
template for another person, the same annual letter a year later). No letter of a real
person: names, addresses and numbers are made up.
"""

import random
import textwrap
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from apps.documents.duplicates_facts import Fact, LetterFacts, numbers_in, payment_reference_in

KINDS: Final = ("tax", "health", "energy", "municipality", "bank", "school")
_REGIONS: Final = ("Noord", "Zuid", "Oost", "West", "Midden", "Delta", "Kust")
_SENDER_NAMES: Final = {
    "tax": "Belastingkantoor",
    "health": "Zorgverzekeraar",
    "energy": "Energie",
    "municipality": "Gemeente",
    "bank": "Spaarbank",
    "school": "Scholengroep",
}
TYPES: Final = {
    "tax": "assessment",
    "health": "invoice",
    "energy": "invoice",
    "municipality": "decision",
    "bank": "information",
    "school": "bill",
}
# Kinds whose letters carry an amount, an invoice number and a payment reference.
PAYING: Final = frozenset({"tax", "health", "energy", "school"})
_FIRST: Final = (
    "Jan", "Anna", "Pieter", "Fatima", "Mohamed", "Sanne", "Daan", "Emma", "Lucas", "Noor",
    "Bram", "Iris", "Thijs", "Lotte", "Ahmed", "Fleur", "Ruben", "Julia", "Sem", "Eva",
)  # fmt: skip
_LAST: Final = (
    "de Vries", "Jansen", "van Dijk", "Bakker", "Visser", "Smit", "Meijer", "de Boer",
    "Mulder", "de Groot", "Bos", "Vos", "Peters", "Hendriks", "van Leeuwen", "Dekker",
    "Brouwer", "de Wit", "Dijkstra", "Kok", "Yilmaz", "El Amrani", "Postma", "Huisman",
)  # fmt: skip
_STREETS: Final = (
    "Wilhelminastraat", "Kerkstraat", "Dorpsstraat", "Stationsweg", "Molenweg", "Julianalaan",
    "Beatrixstraat", "Schoolstraat", "Parallelweg", "Kastanjelaan", "Eikenlaan", "Nieuwstraat",
)  # fmt: skip
_CITIES: Final = (
    "Assen", "Groningen", "Zwolle", "Utrecht", "Tilburg", "Breda", "Leiden", "Arnhem",
    "Haarlem", "Deventer", "Emmen", "Venlo",
)  # fmt: skip
_MONTHS: Final = (
    "januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
    "oktober", "november", "december",
)  # fmt: skip

# Paragraphs every letter of a kind may carry on its further pages (conditions, help).
_BOILERPLATE: Final = {
    "tax": (
        "Bent u het niet eens met deze aanslag? Dan kunt u binnen zes weken na de "
        "dagtekening bezwaar maken. Vermeld daarbij uw burgerservicenummer niet in de "
        "omschrijving, maar gebruik het aanslagnummer.",
        "Wilt u in termijnen betalen? Dan kunt u een betalingsregeling aanvragen. Wij "
        "beoordelen uw verzoek en sturen u daarover een beschikking.",
        "Uitleg bij de berekening: het belastbaar inkomen uit werk en woning is verminderd "
        "met de persoonsgebonden aftrek en vermenigvuldigd met het geldende tarief.",
        "Heeft u een toeslag? Wijzigingen in uw inkomen kunnen gevolgen hebben voor uw "
        "toeslagen. Geef wijzigingen daarom op tijd door.",
    ),
    "health": (
        "Uw basisverzekering vergoedt zorg volgens de polisvoorwaarden. Het verplicht eigen "
        "risico geldt voor de meeste zorgkosten, met uitzondering van de huisarts.",
        "Betaalt u niet op tijd, dan sturen wij u een herinnering. Blijft betaling daarna "
        "uit, dan kunnen wij de vordering overdragen aan een incassobureau.",
        "Specificatie van de premie: basisverzekering, aanvullende verzekering tandarts en "
        "aanvullende verzekering fysiotherapie, verminderd met de collectiviteitskorting.",
        "Wijzigingen in uw gezinssituatie geeft u door via de website of de klantenservice.",
    ),
    "energy": (
        "De leveringstarieven bestaan uit een vast leveringstarief, de netbeheerkosten en "
        "de energiebelasting, verminderd met de vermindering energiebelasting.",
        "Uw termijnbedrag is berekend op basis van uw verwachte verbruik. U kunt het "
        "termijnbedrag zelf aanpassen in de app of op de website.",
        "Klopt een meterstand niet? Geef dan binnen dertig dagen de juiste meterstand door, "
        "dan corrigeren wij de jaarafrekening.",
        "Wilt u overstappen of verhuizen? Meld dit minimaal vier weken van tevoren.",
    ),
    "municipality": (
        "Bezwaar: bent u het niet eens met dit besluit, dan kunt u binnen zes weken een "
        "bezwaarschrift sturen aan het college van burgemeester en wethouders.",
        "Voorlopige voorziening: een bezwaarschrift schorst de werking van het besluit niet. "
        "U kunt de voorzieningenrechter vragen om een voorlopige voorziening.",
        "Persoonsgegevens: de gemeente verwerkt uw gegevens volgens de privacyverklaring.",
        "Heeft u vragen? Bel het klantcontactcentrum op werkdagen tussen negen en vijf uur.",
    ),
    "bank": (
        "De rente op uw spaarrekening is variabel en kan dagelijks wijzigen. De actuele "
        "rentestand vindt u op de website.",
        "Uw tegoed valt onder het depositogarantiestelsel tot het wettelijke maximum per "
        "rekeninghouder.",
        "Controleer de afschriften regelmatig. Ziet u een onbekende afschrijving, neem dan "
        "direct contact met ons op.",
        "Nieuwe voorwaarden gelden vanaf de genoemde ingangsdatum voor alle rekeninghouders.",
    ),
    "school": (
        "De ouderbijdrage is vrijwillig. Ook als u de bijdrage niet betaalt, mag uw kind "
        "meedoen aan alle activiteiten.",
        "De bijdrage wordt besteed aan excursies, de sportdag, de schoolreis en vieringen.",
        "Heeft u een laag inkomen? Dan kunt u een tegemoetkoming aanvragen bij de gemeente.",
        "Vragen over de bijdrage stelt u aan de administratie van de school.",
    ),
}


@dataclass(frozen=True)
class Meta:
    """What a generated letter is about; the text is made from it."""

    kind: str
    sender: str
    sender_index: int
    person: str
    street: str
    city: str
    client_number: str
    letter_date: date
    amount: Decimal | None
    invoice_number: str
    payment_reference: str
    pages: int
    paragraphs: tuple[int, ...]
    reminder: bool = False


@dataclass(frozen=True)
class Letter:
    meta: Meta
    text: str


@dataclass(frozen=True)
class Strength:
    """Which facts the AI read strongly (or a person confirmed), per letter."""

    confirmed: bool
    sender: bool
    letter_date: bool
    amount: bool
    invoice_number: bool
    # A strong reading that is wrong (the AI is sure and mistaken).
    misread: str = ""


def _dutch(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _money(amount: Decimal) -> str:
    whole, cents = f"{amount:.2f}".split(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"€ {'.'.join(groups)},{cents}"


def _reference(rng: random.Random) -> str:
    digits = "".join(str(rng.randrange(10)) for _ in range(16))
    return " ".join(digits[i : i + 4] for i in range(0, 16, 4))


def senders() -> list[tuple[str, str]]:
    """The 40 senders: (kind, name)."""
    found = []
    for i in range(40):
        kind = KINDS[i % len(KINDS)]
        found.append((kind, f"{_SENDER_NAMES[kind]} {_REGIONS[i % len(_REGIONS)]} {i:02d}"))
    return found


def new_meta(rng: random.Random, sender_index: int, today: date) -> Meta:
    kind, sender = senders()[sender_index]
    paying = kind in PAYING
    return Meta(
        kind=kind,
        sender=sender,
        sender_index=sender_index,
        person=f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
        street=f"{rng.choice(_STREETS)} {rng.randrange(1, 180)}",
        city=f"{rng.randrange(1000, 9999)} {rng.choice('ABCDEFGHJKLMNPRSTVWXZ')}"
        f"{rng.choice('ABCDEFGHJKLMNPRSTVWXZ')} {rng.choice(_CITIES)}",
        client_number=f"{rng.randrange(10**7, 10**8):08d}",
        letter_date=today - timedelta(days=rng.randrange(0, 400)),
        amount=Decimal(rng.randrange(1500, 250000)) / 100 if paying else None,
        invoice_number=f"{kind[:2].upper()}{rng.randrange(10**6, 10**7)}" if paying else "",
        payment_reference=_reference(rng) if paying else "",
        pages=rng.choice((1, 1, 2, 2, 3, 4)),
        paragraphs=tuple(rng.sample(range(4), 4)),
    )


def render(meta: Meta) -> str:
    """The letter's text: pages joined by a form feed, as the upload stores them."""
    head = [
        meta.sender,
        f"Postbus {1000 + meta.sender_index * 37}, {meta.city.split(' ', 2)[2]}",
        "",
        meta.person,
        meta.street,
        meta.city,
        "",
        f"Klantnummer {meta.client_number}",
    ]
    if meta.invoice_number:
        head.append(f"Factuurnummer {meta.invoice_number}")
    head.append(f"Datum {_dutch(meta.letter_date)}")
    if meta.reminder:
        head += [
            "",
            "HERINNERING",
            "Wij hebben uw betaling voor onderstaande factuur nog niet ontvangen. Wilt u het "
            "openstaande bedrag alsnog binnen veertien dagen overmaken? Heeft u inmiddels "
            "betaald, dan kunt u deze herinnering als niet verzonden beschouwen.",
        ]
    body = _body(meta)
    footer = [
        "",
        "Met vriendelijke groet,",
        f"Klantenservice {meta.sender}",
        f"Pagina 1 van {meta.pages}",
    ]
    pages = ["\n".join([*head, "", *body, *footer])]
    for page in range(2, meta.pages + 1):
        paragraph = _BOILERPLATE[meta.kind][meta.paragraphs[(page - 2) % 4]]
        lines = [
            f"{meta.sender} - klantnummer {meta.client_number}",
            "",
            textwrap.fill(paragraph, 80),
            "",
            *_specification(meta, page),
            "",
            f"Pagina {page} van {meta.pages}",
        ]
        pages.append("\n".join(lines))
    return "\f".join(pages)


def _body(meta: Meta) -> list[str]:
    day = _dutch(meta.letter_date)
    due = _dutch(meta.letter_date + timedelta(days=28))
    amount = _money(meta.amount) if meta.amount is not None else ""
    texts = {
        "tax": f"Geachte {meta.person}, hierbij ontvangt u de aanslag inkomstenbelasting over "
        f"het belastingjaar {meta.letter_date.year - 1}. Het te betalen bedrag is {amount}. "
        f"Betaal dit bedrag uiterlijk {due} onder vermelding van het betalingskenmerk "
        f"{meta.payment_reference}. Het aanslagnummer staat bovenaan deze brief.",
        "health": f"Geachte {meta.person}, dit is uw premienota voor de maand "
        f"{_MONTHS[meta.letter_date.month - 1]} {meta.letter_date.year}. De premie voor uw "
        f"zorgverzekering bedraagt {amount}. Wij schrijven het bedrag rond {due} af van uw "
        f"rekening. Betalingskenmerk {meta.payment_reference}.",
        "energy": f"Geachte {meta.person}, hierbij ontvangt u de jaarafrekening voor de "
        f"levering van elektriciteit en aardgas op het adres {meta.street}. Het totaalbedrag "
        f"van de jaarafrekening is {amount}. Wij schrijven dit bedrag rond {due} af. Het "
        f"betalingskenmerk is {meta.payment_reference}. Meterstand normaaltarief "
        f"{int(meta.client_number) % 99991:06d}.",
        "municipality": f"Geachte {meta.person}, op {day} hebben wij uw aanvraag voor een "
        f"parkeervergunning voor het adres {meta.street} ontvangen. Wij hebben besloten uw "
        "aanvraag toe te kennen. De vergunning is geldig voor een jaar en gaat in op de "
        "eerste dag van de volgende maand.",
        "bank": f"Geachte {meta.person}, per {day} wijzigt de rente op uw spaarrekening met "
        f"klantnummer {meta.client_number}. U hoeft hiervoor niets te doen. In de bijlage "
        "vindt u het overzicht van de nieuwe rentestanden en de voorwaarden.",
        "school": f"Geachte ouder of verzorger van {meta.person.split()[0]}, hierbij ontvangt "
        f"u het verzoek voor de vrijwillige ouderbijdrage van het schooljaar "
        f"{meta.letter_date.year}-{meta.letter_date.year + 1}. De bijdrage is {amount}. "
        f"Wilt u het bedrag voor {due} overmaken met kenmerk {meta.payment_reference}?",
    }
    return textwrap.wrap(texts[meta.kind], 80)


def _specification(meta: Meta, page: int) -> list[str]:
    if meta.amount is None:
        return [f"Referentie {meta.client_number}-{page}"]
    share = (meta.amount / (page + 1)).quantize(Decimal("0.01"))
    return [
        f"Specificatie deel {page}: {_money(share)}",
        f"Periode tot en met {_dutch(meta.letter_date + timedelta(days=page))}",
    ]


# -- the OCR noise model --------------------------------------------------------------------

_LOOKALIKE: Final = {
    "l": "1",
    "1": "l",
    "O": "0",
    "0": "O",
    ",": ".",
    ".": ",",
    "e": "c",
    "a": "o",
    "i": "l",
    "n": "u",
    "o": "a",
    "t": "f",
    "s": "5",
    "B": "8",
    "h": "b",
}


def noisy(text: str, rate: float, rng: random.Random) -> str:
    """Character confusions (rn/m, l/1, O/0, comma/period and look-alike letters) at
    `rate`, and lines wrapped at another width."""
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if rng.random() < rate:
            if text.startswith("rn", i):
                out.append("m")
                i += 2
                continue
            if char == "m":
                out.append("rn")
                i += 1
                continue
            if char in _LOOKALIKE:
                out.append(_LOOKALIKE[char])
                i += 1
                continue
        out.append(char)
        i += 1
    pages = "".join(out).split("\f")
    width = rng.randrange(60, 100)
    return "\f".join(_rewrap(page, width) for page in pages)


def _rewrap(page: str, width: int) -> str:
    blocks = page.split("\n\n")
    return "\n\n".join(
        textwrap.fill(" ".join(block.split("\n")), width) if len(block) > 80 else block
        for block in blocks
    )


def scan_again(letter: Letter, rng: random.Random) -> str:
    """The same letter scanned a second time: 0.5 to 3% confusions, other line breaks, a
    header or footer line lost; the last page missing in 10%, pages in another order in
    5%."""
    pages = letter.text.split("\f")
    if len(pages) > 1 and rng.random() < 0.10:
        pages = pages[:-1]
    if len(pages) > 1 and rng.random() < 0.05:
        rng.shuffle(pages)
    lines = pages[0].split("\n")
    if rng.random() < 0.5:
        lines = lines[1:] if rng.random() < 0.5 else lines[:-1]
    pages[0] = "\n".join(lines)
    return noisy("\f".join(pages), rng.uniform(0.005, 0.03), rng)


# -- the hard negatives ---------------------------------------------------------------------

NEGATIVE_KINDS: Final = ("reminder", "instalment", "next_month", "other_person", "next_year")


def hard_negative(meta: Meta, how: str, rng: random.Random) -> Meta:
    """Another letter that looks like `meta`'s (paying kinds only)."""
    later = meta.letter_date + timedelta(days=rng.randrange(25, 40))
    other_amount = (meta.amount or Decimal(0)) + Decimal(rng.randrange(100, 9000)) / 100
    if how == "reminder":
        return replace(meta, letter_date=later, reminder=True)
    if how == "instalment":
        return replace(meta, letter_date=later, amount=other_amount)
    if how == "next_month":
        return replace(
            meta,
            letter_date=later,
            amount=other_amount,
            invoice_number=f"{meta.invoice_number[:2]}{rng.randrange(10**6, 10**7)}",
            payment_reference=_reference(rng),
        )
    if how == "other_person":
        other = new_meta(rng, meta.sender_index, meta.letter_date)
        return replace(other, letter_date=meta.letter_date, pages=meta.pages)
    return replace(
        meta,
        letter_date=meta.letter_date.replace(year=meta.letter_date.year + 1),
        amount=other_amount,
        invoice_number=f"{meta.invoice_number[:2]}{rng.randrange(10**6, 10**7)}",
        payment_reference=_reference(rng),
    )


# -- the facts the decision sees -------------------------------------------------------------


def strength(rng: random.Random, *, already_there: bool, misread_rate: float) -> Strength:
    """A letter already there is confirmed in 60%; otherwise the AI reads each fact strongly
    in 80%, and a strong reading is wrong in `misread_rate`."""
    confirmed = already_there and rng.random() < 0.6
    misread = ""
    if not already_there and rng.random() < misread_rate:
        misread = rng.choice(("letter_date", "amount"))
    return Strength(
        confirmed=confirmed,
        sender=confirmed or rng.random() < 0.8,
        letter_date=confirmed or rng.random() < 0.8,
        amount=rng.random() < 0.8,
        invoice_number=rng.random() < 0.8,
        misread=misread,
    )


def facts(text: str, meta: Meta, power: Strength, *, guards: bool = True) -> LetterFacts:
    """The facts `duplicates.facts_of` would build for the letter (strong as `power` says;
    without guards every fact is weak, so no guard can fire)."""

    def fact(value: str, is_strong: bool) -> Fact | None:
        return Fact(value, is_strong and guards) if value else None

    letter_date = meta.letter_date
    amount = meta.amount
    if power.misread == "letter_date" and power.letter_date:
        letter_date += timedelta(days=1)
    if power.misread == "amount" and power.amount and amount is not None:
        amount += Decimal("10")
    reference = payment_reference_in(text)
    doc_type = "reminder" if meta.reminder else TYPES[meta.kind]
    return LetterFacts(
        text=text,
        numbers=numbers_in(text),
        payment_reference=None if reference is None else Fact(reference, strong=guards),
        sender=fact(meta.sender, power.sender),
        letter_date=fact(letter_date.isoformat(), power.letter_date),
        document_type=fact(doc_type, power.confirmed),
        amount=fact(f"{amount:.2f}" if amount is not None else "", power.amount),
        invoice_number=fact(meta.invoice_number, power.invoice_number),
        page_count=text.count("\f") + 1,
    )
