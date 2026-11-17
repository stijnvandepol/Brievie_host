import os
import sys
from argparse import ArgumentParser
from getpass import getpass
from urllib.parse import urlsplit
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from apps.pipeline import settings_service
from apps.pipeline.models import AISettings
from apps.pipeline.settings_service import Target
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

KEY_VARIABLE = "BRIEVIE_AI_API_KEY"
ACCEPT_FLAG = "--accept-external-ai"
# What the administrator types to agree; compared without case and outer spaces.
ACCEPT_WORD = "akkoord"


class Command(BaseCommand):
    help = (
        "Set how a workspace has its documents read: off, external (an AI service: "
        "--supplier mistral (default), openai or claude; --base-url and --model "
        "override it, e.g. for another OpenAI-compatible service) or local (a service on "
        "the own network). Switching to an external service shows what leaves the server "
        f"and asks to type '{ACCEPT_WORD}'; a script passes {ACCEPT_FLAG}. The API key is "
        f"read from {KEY_VARIABLE} or asked for; it is never a command-line argument."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--workspace", required=True, type=UUID)
        parser.add_argument("--mode", required=True, choices=AISettings.Mode.values)
        parser.add_argument(
            "--supplier",
            choices=list(settings_service.SUPPLIERS),
            default=settings_service.DEFAULT_SUPPLIER,
            help="The AI service for mode external.",
        )
        parser.add_argument("--base-url", default="")
        parser.add_argument("--model", default="")
        parser.add_argument(
            ACCEPT_FLAG,
            action="store_true",
            dest="accept_external_ai",
            help=(
                "For scripts, instead of typing the word. You state that you agree, for "
                "everyone whose post is in the workspace, that the BSN-masked text of every "
                "letter and the names of the workspace's labels and persons go to the "
                "external AI service, under that service's own terms."
            ),
        )

    def handle(
        self,
        *args: object,
        workspace: UUID,
        mode: str,
        supplier: str,
        base_url: str,
        model: str,
        accept_external_ai: bool,
        **options: object,
    ) -> None:
        found = Workspace.objects.filter(id=workspace).first()
        if found is None:
            raise CommandError(f"There is no workspace {workspace}.")
        try:
            target = settings_service.resolve(
                mode=mode, supplier=supplier, base_url=base_url, model=model
            )
            with workspace_context(workspace):
                asks = target.mode == AISettings.Mode.EXTERNAL and not (
                    settings_service.consent_in_force(workspace, target.base_url)
                )
            if asks:
                self.stdout.write(external_notice(found.name, target))
                self._confirm(accepted=accept_external_ai)
            elif target.mode == AISettings.Mode.LOCAL:
                self.stdout.write(
                    _(
                        "De AI op je eigen netwerk leest de tekst van elke brief. "
                        "Er gaat niets naar een dienst buiten je netwerk."
                    )
                )
            api_key = None
            if mode == AISettings.Mode.EXTERNAL:
                api_key = os.environ.get(KEY_VARIABLE) or getpass("API key: ")
            elif mode == AISettings.Mode.LOCAL:
                api_key = os.environ.get(KEY_VARIABLE) or None
            with workspace_context(workspace):
                settings = settings_service.configure(
                    workspace_id=workspace,
                    mode=mode,
                    supplier=supplier,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    consent=asks,
                )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        summary = f"AI for workspace {workspace}: {settings.mode}"
        if settings.mode != AISettings.Mode.OFF:
            summary += f" ({settings.base_url}, model {settings.model})"
        self.stdout.write(summary)

    def _confirm(self, *, accepted: bool) -> None:
        """Stop unless the administrator agrees: with the flag, or by typing the word."""
        if accepted:
            self.stdout.write(
                _("Je hebt %(flag)s meegegeven: daarmee ga je hiermee akkoord.")
                % {"flag": ACCEPT_FLAG}
            )
            return
        if not sys.stdin.isatty():
            raise CommandError(
                _(
                    "AI is niet aangezet. Er is geen toetsenbord om akkoord te geven. "
                    "Geef in een script %(flag)s mee als je akkoord gaat."
                )
                % {"flag": ACCEPT_FLAG}
            )
        self.stdout.write(
            _('Typ "%(word)s" om AI aan te zetten: ') % {"word": ACCEPT_WORD}, ending=""
        )
        self.stdout.flush()
        if sys.stdin.readline().strip().lower() != ACCEPT_WORD:
            raise CommandError(_("AI is niet aangezet. Je hebt geen akkoord gegeven."))


def external_notice(workspace_name: str, target: Target) -> str:
    """What the administrator must know before letters go to an external AI service."""
    preset = settings_service.SUPPLIERS.get(target.supplier)
    if preset is None:
        # Behind one host a path can lead to another service: show the whole address.
        parts = urlsplit(target.base_url)
        service = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"
        country = _("onbekend. Zoek zelf op in welk land deze dienst staat.")
    else:
        service = f"{preset.name} ({target.host})"
        country = str(preset.country)
    return _(
        "Je zet AI aan voor werkruimte %(workspace)s. Lees dit eerst.\n"
        "\n"
        "Dit gaat naar de AI-dienst, bij elke brief:\n"
        "- De tekst van elke brief. Een BSN is daarin vervangen door [BSN]. Andere "
        "gegevens staan er gewoon in, ook over geld, gezondheid en andere mensen.\n"
        "- De namen van de labels in deze werkruimte.\n"
        "- De namen van de personen in deze werkruimte, ook andere schrijfwijzen van "
        "hun naam.\n"
        "\n"
        "Dit gaat er nooit naartoe:\n"
        "- De foto's of scans van de pagina's.\n"
        "- Je regels en de interne nummers van brieven, labels en personen.\n"
        "\n"
        "Hier gaat het naartoe:\n"
        "- Dienst: %(service)s, model %(model)s\n"
        "- Land: %(country)s\n"
        "\n"
        "Let op:\n"
        "- Wat de dienst met de tekst doet en hoe lang de dienst die bewaart, staat in "
        "de voorwaarden van de dienst zelf. Brievie heeft daar geen invloed op.\n"
        "- Jij beslist dit voor iedereen van wie post in deze werkruimte staat. Vertel "
        "het hun.\n"
        "- Brievie legt vast dat je akkoord bent, en wanneer. Elk lid van de werkruimte "
        "kan zien dat AI aan staat.\n"
        "- Je kunt AI weer uitzetten met --mode off. Wat al verstuurd is, haal je "
        "daarmee niet terug.\n"
    ) % {
        "workspace": workspace_name,
        "service": service,
        "model": target.model,
        "country": country,
    }
