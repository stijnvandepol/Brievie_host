"""Example rules a user can start from: the gallery on the Automatisering page.

A template is not a rule. It is a rule with blanks: the words or the name the user types
(`TemplateCondition.value` empty, with an `example`), and always the label or person the
user picks (a template never names one; `label_key` only suggests a start-set label when
the workspace has it). Opening one fills the rule editor; nothing is stored until the
user saves. tests/test_rules_templates.py fills every blank and checks the result
against the real rule definition and the engine.

Every template proposes (mode `propose`), as a new rule does, and its sentence says so.
"""

from dataclasses import dataclass

from django.utils.translation import gettext as _

from apps.rules.models import Rule
from apps.workspaces.models import Workspace

_HOME = Workspace.Kind.HOME
_BUSINESS = Workspace.Kind.BUSINESS


@dataclass(frozen=True)
class TemplateCondition:
    field: str
    op: str
    # Filled in already; empty means the user types it.
    value: str = ""
    # What to type, as a placeholder ("Zilveren Kruis").
    example: str = ""
    # One sentence: what goes here.
    hint: str = ""


@dataclass(frozen=True)
class TemplateAction:
    type: str
    hint: str
    # A start-set label to pick when the workspace has it (matched on `system_key`).
    label_key: str = ""
    label_name: str = ""


@dataclass(frozen=True)
class RuleTemplate:
    key: str
    # The workspace kinds it is meant for (home, business or both).
    kinds: tuple[str, ...]
    title: str
    # What the rule does, in one sentence a non-technical person uses.
    description: str
    # The rule's name in the editor; the user can change it.
    name: str
    match: str
    conditions: tuple[TemplateCondition, ...]
    actions: tuple[TemplateAction, ...]
    # A caveat worth knowing before saving; may be empty.
    tip: str = ""
    mode: str = Rule.Mode.PROPOSE


def _sender(example: str) -> TemplateCondition:
    return TemplateCondition(
        "sender",
        "contains_word",
        example=example,
        hint=_("Typ de naam van de afzender, zoals die op de brief staat."),
    )


def _pick_label(hint: str, key: str = "", name: str = "") -> TemplateAction:
    return TemplateAction("add_label", hint=hint, label_key=key, label_name=name)


def _pick_person(hint: str) -> TemplateAction:
    return TemplateAction("add_addressee", hint=hint)


def _anywhere() -> str:
    return _(
        "Het woord mag overal in de brief staan: ook een brief die het woord alleen noemt, "
        "krijgt het voorstel."
    )


def rule_templates() -> tuple[RuleTemplate, ...]:
    """The gallery in the order it shows, in the active language."""
    return (
        RuleTemplate(
            key="label_by_sender",
            kinds=(_HOME, _BUSINESS),
            title=_("Label op afzender"),
            description=_(
                "Stelt een label voor op alle post van één afzender, bijvoorbeeld het "
                "label Zorg op post van je zorgverzekeraar."
            ),
            name=_("Label op afzender"),
            match="all",
            conditions=(_sender("Zilveren Kruis"),),
            actions=(_pick_label(_("Kies het label dat de brief krijgt.")),),
            tip=_(
                "Preciezer dan een woord in de brief: alleen post die van deze afzender komt, past."
            ),
        ),
        RuleTemplate(
            key="label_by_word",
            kinds=(_HOME, _BUSINESS),
            title=_("Label op een woord in de brief"),
            description=_(
                "Stelt een label voor op post waarin een bepaald woord staat, bijvoorbeeld "
                "het label Auto als er APK in staat."
            ),
            name=_("Label op een woord"),
            match="all",
            conditions=(
                TemplateCondition(
                    "text",
                    "contains_word",
                    example="APK",
                    hint=_("Typ het woord waar de brief om draait."),
                ),
            ),
            actions=(_pick_label(_("Kies het label dat de brief krijgt.")),),
            tip=_anywhere()
            + " "
            + _("Weet je van wie de post komt? Kies dan liever 'Label op afzender'."),
        ),
        RuleTemplate(
            key="person_by_sender",
            kinds=(_HOME, _BUSINESS),
            title=_("Alles van deze afzender voor dezelfde persoon"),
            description=_(
                "Stelt voor om alle post van één afzender op naam van dezelfde persoon te "
                "zetten, bijvoorbeeld de post van DUO voor je kind."
            ),
            name=_("Afzender voor één persoon"),
            match="all",
            conditions=(_sender("DUO"),),
            actions=(_pick_person(_("Kies voor wie de post is.")),),
        ),
        RuleTemplate(
            key="person_by_word",
            kinds=(_HOME, _BUSINESS),
            title=_("Op naam zetten op een woord in de brief"),
            description=_(
                "Stelt voor om post op naam van een persoon te zetten als er een bepaald "
                "woord in staat, bijvoorbeeld de naam van de school van je kind."
            ),
            name=_("Op naam op een woord"),
            match="all",
            conditions=(
                TemplateCondition(
                    "text",
                    "contains_word",
                    example="Basisschool De Regenboog",
                    hint=_("Typ een woord of naam die alleen in post voor deze persoon staat."),
                ),
            ),
            actions=(_pick_person(_("Kies voor wie de post is.")),),
            tip=_anywhere(),
        ),
        RuleTemplate(
            key="supplier_invoice",
            kinds=(_BUSINESS,),
            title=_("Facturen van een leverancier"),
            description=_(
                "Stelt een label voor op facturen van één leverancier, bijvoorbeeld een "
                "eigen label per leverancier naast Inkoop."
            ),
            name=_("Facturen van een leverancier"),
            match="all",
            conditions=(
                TemplateCondition(
                    "document_type",
                    "is",
                    value="invoice",
                    hint=_("Alleen facturen."),
                ),
                _sender("Makro"),
            ),
            actions=(_pick_label(_("Kies het label voor deze leverancier.")),),
            tip=_(
                "Beide voorwaarden moeten kloppen. Het soort post en de afzender zijn "
                "bekend als de AI de brief heeft gelezen, of zodra jij hem bevestigt."
            ),
        ),
        RuleTemplate(
            key="municipality_woz",
            kinds=(_HOME,),
            title=_("De WOZ-beschikking van de gemeente"),
            description=_(
                "Stelt het label Wonen voor op post van de gemeente waarin het woord WOZ "
                "staat. Beide voorwaarden moeten kloppen."
            ),
            name=_("WOZ van de gemeente"),
            match="all",
            conditions=(
                TemplateCondition(
                    "sender",
                    "contains_word",
                    value="gemeente",
                    hint=_("De afzender heeft het woord gemeente in zijn naam."),
                ),
                TemplateCondition(
                    "text",
                    "contains_word",
                    value="WOZ",
                    hint=_("En het woord WOZ staat in de brief."),
                ),
            ),
            actions=(
                _pick_label(_("Kies het label dat de brief krijgt."), key="wonen", name="Wonen"),
            ),
            tip=_(
                "Twee voorwaarden samen zijn preciezer dan één: een brief van je "
                "verzekeraar die de WOZ-waarde alleen noemt, past niet."
            ),
        ),
        RuleTemplate(
            key="reminders",
            kinds=(_HOME, _BUSINESS),
            title=_("Herinneringen en aanmaningen"),
            description=_(
                "Stelt een label voor op elke herinnering of aanmaning, zodat je ze snel "
                "terugvindt."
            ),
            name=_("Herinneringen en aanmaningen"),
            match="any",
            conditions=(
                TemplateCondition("document_type", "is", value="reminder"),
                TemplateCondition("document_type", "is", value="demand"),
            ),
            actions=(_pick_label(_("Kies het label, bijvoorbeeld een eigen label Betalen.")),),
            tip=_(
                "Een van de voorwaarden is genoeg. Het soort post is bekend als de AI de "
                "brief heeft gelezen, of zodra jij hem bevestigt."
            ),
        ),
    )
