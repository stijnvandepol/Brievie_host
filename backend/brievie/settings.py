from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# On the host (make test, make migrate) the values come from deploy/.env;
# in the container they come from the environment that Compose sets.
_dotenv = BASE_DIR.parent / "deploy" / ".env"
if _dotenv.is_file():
    env.read_env(str(_dotenv))

SECRET_KEY: str = env.str("SECRET_KEY")
DEBUG: bool = env.bool("DEBUG", default=False)
ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=["localhost"])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "ninja",
    "apps.accounts",
    "apps.workspaces",
    "apps.documents",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.errors.ApiMethodNotAllowedMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "brievie.urls"
WSGI_APPLICATION = "brievie.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
            ],
        },
    },
]

# Two PostgreSQL roles (see deploy/postgres-init/01-roles.sh). Requests use the
# app role, which can only read and write rows. The owner role owns the schema;
# only the one-shot migrate container (BRIEVIE_DB_ROLE=owner) and the test run
# (tests/conftest.py) get its credentials, never the serving process.
_use_owner_role = env.str("BRIEVIE_DB_ROLE", default="app") == "owner"
DATABASE_OWNER: dict[str, object] | None = (
    env.db("DATABASE_OWNER_URL") if env.str("DATABASE_OWNER_URL", default="") else None
)
if _use_owner_role and DATABASE_OWNER is None:
    raise ImproperlyConfigured("BRIEVIE_DB_ROLE=owner needs DATABASE_OWNER_URL.")
DATABASES = {
    "default": DATABASE_OWNER if _use_owner_role and DATABASE_OWNER else env.db("DATABASE_URL")
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "nl"
LANGUAGES = [("nl", "Nederlands")]
TIME_ZONE = "Europe/Amsterdam"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# The server is reachable from the internet behind Caddy, which terminates TLS.
# Local http development can switch the secure cookies off in deploy/.env.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE: bool = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE: bool = env.bool("CSRF_COOKIE_SECURE", default=True)
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS: list[str] = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SECURE_HSTS_SECONDS: int = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
