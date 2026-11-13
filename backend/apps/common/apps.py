from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "apps.common"
    label = "common"

    def ready(self) -> None:
        from apps.common import checks  # noqa: F401  (registers the system checks)
