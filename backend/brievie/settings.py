from datetime import timedelta
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
if not DEBUG and ("change-me" in SECRET_KEY or len(SECRET_KEY) < 50):
    raise ImproperlyConfigured(
        "SECRET_KEY is still the placeholder or shorter than 50 characters. "
        "Generate one: openssl rand -base64 48 (see 'Run the whole stack' in the README)."
    )
ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=["localhost"])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "ninja",
    "axes",
    "procrastinate.contrib.django",
    "apps.common",
    "apps.accounts",
    "apps.workspaces",
    "apps.documents",
    "apps.people",
    "apps.labels",
    "apps.tasks",
    "apps.rules",
    "apps.pipeline",
    "apps.devices",
]

AUTH_USER_MODEL = "accounts.User"

# Axes first: it refuses a locked-out email + address before the password is checked.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.errors.ApiMethodNotAllowedMiddleware",
    "apps.common.errors.ApiContentLengthMiddleware",
    "apps.common.errors.ApiJsonBodyMiddleware",
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

# The database cache is shared by all gunicorn workers (throttle counts).
# `manage.py createcachetable` creates its table; the test run does so itself.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "brievie_cache",
    }
}

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

# The built web app (web/dist, copied here by deploy/Dockerfile). WhiteNoise serves its
# files from the site root; apps.common.web serves its index for every other non-API path.
BRIEVIE_WEB_DIST = Path(env.str("BRIEVIE_WEB_DIST", default=str(BASE_DIR / "webdist")))
if BRIEVIE_WEB_DIST.is_dir():
    WHITENOISE_ROOT = BRIEVIE_WEB_DIST


# Vite puts a content hash in every file name under /assets/.
def _is_hashed_asset(path: str, url: str) -> bool:
    return url.startswith("/assets/")


WHITENOISE_IMMUTABLE_FILE_TEST = _is_hashed_asset

# The server is reachable from the internet behind Caddy, which terminates TLS.
# Local http development can switch the secure cookies off in deploy/.env.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE: bool = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE: bool = env.bool("CSRF_COOKIE_SECURE", default=True)
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS: list[str] = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# One variable for Django and Caddy. Compose sets 0 by default: with the private CA
# a browser must still be able to click through its certificate warning, which
# HSTS forbids. Set 31536000 with a public certificate.
SECURE_HSTS_SECONDS: int = env.int("BRIEVIE_HSTS_SECONDS", default=31536000)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Number of reverse proxies in front of the app (Caddy in Compose: 1). Decides
# which X-Forwarded-For entry is the client address (apps.common.net.client_ip).
BRIEVIE_PROXY_COUNT: int = env.int("BRIEVIE_PROXY_COUNT", default=0)

# Login lockout (django-axes): 5 failures for the same email + address lock that
# pair for 15 minutes. apps.accounts.services.login checks the lock itself and
# answers 429 in the API error shape, so axes' middleware is not installed.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_USERNAME_FORM_FIELD = "username"
AXES_USERNAME_CALLABLE = "apps.accounts.services.axes_username"
AXES_CLIENT_IP_CALLABLE = "apps.common.net.client_ip"
AXES_RESET_ON_SUCCESS = True
AXES_DISABLE_ACCESS_LOG = True
AXES_VERBOSE = False
SILENCED_SYSTEM_CHECKS = ["axes.W002"]  # AxesMiddleware: see above.

# Addresses the app uses to reach this server, local address first; each must be
# https://. They go into the pairing QR code.
BRIEVIE_PUBLIC_URLS: list[str] = [
    url.strip() for url in env.list("BRIEVIE_PUBLIC_URLS", default=[]) if url.strip()
]
if any(not url.startswith("https://") for url in BRIEVIE_PUBLIC_URLS):
    raise ImproperlyConfigured("Every BRIEVIE_PUBLIC_URLS entry must start with https://.")

# Root certificate of Caddy's internal CA, from Caddy's data volume (read-only).
# Empty: no private CA (public certificate); pairing then pins nothing.
BRIEVIE_CA_CERT_PATH: str = env.str(
    "BRIEVIE_CA_CERT_PATH", default="/caddy-data/caddy/pki/authorities/local/root.crt"
)

# First-run setup needs this code. Unset: `manage.py prepare_setup_token` (run when
# the api container starts) generates one, stores its hash and prints it once.
BRIEVIE_SETUP_TOKEN: str = env.str("BRIEVIE_SETUP_TOKEN", default="")
if BRIEVIE_SETUP_TOKEN and len(BRIEVIE_SETUP_TOKEN) < 16:
    raise ImproperlyConfigured("BRIEVIE_SETUP_TOKEN must be at least 16 characters long.")

# Largest request body. Caddy enforces the same value (request_body max_size), so a
# too-large body gets 413 from the proxy instead of a connection reset. The document
# upload endpoint (phase 1b) will need its own, larger limit.
BRIEVIE_MAX_BODY_BYTES: int = env.int("BRIEVIE_MAX_BODY_BYTES", default=2621440)
DATA_UPLOAD_MAX_MEMORY_SIZE: int = BRIEVIE_MAX_BODY_BYTES

# A document upload (page images plus their recognised text) has its own, larger
# limit; Caddy enforces the same one on that route.
BRIEVIE_MAX_UPLOAD_BYTES: int = env.int("BRIEVIE_MAX_UPLOAD_BYTES", default=33554432)
BRIEVIE_MAX_PAGES: int = env.int("BRIEVIE_MAX_PAGES", default=30)
BRIEVIE_MAX_PAGE_TEXT_CHARS: int = env.int("BRIEVIE_MAX_PAGE_TEXT_CHARS", default=20000)
# Uploaded files stay in memory, whatever their size: a temporary file would be an
# unencrypted copy of a page on disk. Set globally, because the session's CSRF check
# parses the form before any view could choose its own handlers.
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]
FILE_UPLOAD_MAX_MEMORY_SIZE: int = BRIEVIE_MAX_UPLOAD_BYTES

# Background jobs live in each app's jobs.py ("tasks" is the name of a domain app).
PROCRASTINATE_AUTODISCOVER_MODULE_NAME = "jobs"

# Wraps every workspace's data key. Read lazily (apps.common.crypto.master_key), so
# migrate and export_openapi run without it; the api and worker check it at start.
BRIEVIE_MASTER_KEY: str = env.str("BRIEVIE_MASTER_KEY", default="")
# Encrypted document files. A volume in Compose; never a path elsewhere in the code.
BRIEVIE_STORAGE_ROOT: str = env.str("BRIEVIE_STORAGE_ROOT", default="/data/documents")

# Every handler masks BSNs: no log line may carry one, whatever code wrote it.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"mask_bsn": {"()": "apps.common.privacy.BsnMaskingFilter"}},
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "filters": ["mask_bsn"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
