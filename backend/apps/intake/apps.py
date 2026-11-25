from django.apps import AppConfig


class IntakeConfig(AppConfig):
    """Files from a browser (and later the scan folder) become documents (intake spec)."""

    name = "apps.intake"
    label = "intake"
