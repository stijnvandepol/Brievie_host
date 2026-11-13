from pathlib import Path

import django_stubs_ext
import environ

# Makes generic Django classes (e.g. ModelAdmin[User]) subscriptable at runtime.
django_stubs_ext.monkeypatch()

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
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ninja",
    "apps.accounts",
    "apps.workspaces",
    "apps.documents",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Two PostgreSQL roles (see deploy/postgres-init/01-roles.sh). Requests use the
# app role, which can only read and write rows. The owner role owns the schema:
# migrations run with BRIEVIE_DB_ROLE=owner and the test suite switches to it in
# tests/conftest.py.
DATABASE_OWNER: dict[str, object] = env.db("DATABASE_OWNER_URL")
_use_owner_role = env.str("BRIEVIE_DB_ROLE", default="app") == "owner"
DATABASES = {"default": env.db("DATABASE_OWNER_URL" if _use_owner_role else "DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "nl"
TIME_ZONE = "Europe/Amsterdam"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
