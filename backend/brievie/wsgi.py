import os

from django.core.wsgi import get_wsgi_application

from apps.common.hardening import forbid_inspection

# gunicorn's master did so already (brievie.gunicorn_config); another server may not.
forbid_inspection()

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "brievie.settings")

application = get_wsgi_application()
