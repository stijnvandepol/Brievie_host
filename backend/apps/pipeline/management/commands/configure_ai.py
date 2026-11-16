import os
from argparse import ArgumentParser
from getpass import getpass
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.pipeline import settings_service
from apps.pipeline.models import AISettings
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

KEY_VARIABLE = "BRIEVIE_AI_API_KEY"


class Command(BaseCommand):
    help = (
        "Set how a workspace has its documents read: off, external (an AI service: "
        "--supplier mistral (default), openai or claude; --base-url and --model "
        "override it, e.g. for another OpenAI-compatible service) or local (a service on "
        "the own network). The API key is read "
        f"from {KEY_VARIABLE} or asked for; it is never a command-line argument."
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

    def handle(
        self,
        *args: object,
        workspace: UUID,
        mode: str,
        supplier: str,
        base_url: str,
        model: str,
        **options: object,
    ) -> None:
        if not Workspace.objects.filter(id=workspace).exists():
            raise CommandError(f"There is no workspace {workspace}.")
        api_key = None
        if mode == AISettings.Mode.EXTERNAL:
            api_key = os.environ.get(KEY_VARIABLE) or getpass("API key: ")
        elif mode == AISettings.Mode.LOCAL:
            api_key = os.environ.get(KEY_VARIABLE) or None
        try:
            with workspace_context(workspace):
                settings = settings_service.configure(
                    workspace_id=workspace,
                    mode=mode,
                    supplier=supplier,
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        summary = f"AI for workspace {workspace}: {settings.mode}"
        if settings.mode != AISettings.Mode.OFF:
            summary += f" ({settings.base_url}, model {settings.model})"
        self.stdout.write(summary)
