from django.apps import AppConfig


class AccessConfig(AppConfig):
    """External access (S "Van buiten bereikbaar"): no models, no migrations."""

    name = "apps.access"
    label = "access"
