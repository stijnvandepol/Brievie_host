import os
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

from apps.common import secrets as _secrets

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
# On the host (make test, make migrate) the values come from deploy/.env;
# in the container they come from the environment that Compose sets.
_dotenv = BASE_DIR.parent / "deploy" / ".env"
if _dotenv.is_file():
    env.read_env(str(_dotenv))


def _secret(name: str, default: str | None = None) -> str:
    """A secret from `NAME` or the file `NAME_FILE` names (apps.common.secrets).

    Without a default a missing secret stops the start. Errors carry no content."""
    try:
        value = _secrets.read_secret(name, os.environ, default=default or "")
    except _secrets.SecretFileError as error:
        raise ImproperlyConfigured(f"{name}_FILE cannot be used ({error.code}).") from None
    if not value and default is None:
        raise ImproperlyConfigured(f"Set {name} or {name}_FILE.")
    return value


def _database_url(name: str) -> str:
    try:
        return _secrets.database_url(name, os.environ)
    except _secrets.SecretFileError as error:
        raise ImproperlyConfigured(f"{name} password file cannot be used ({error.code}).") from None


SECRET_KEY: str = _secret("SECRET_KEY")
DEBUG: bool = env.bool("DEBUG", default=False)
_secret_key_problem = _secrets.check_secret_key(SECRET_KEY, debug=DEBUG)
if _secret_key_problem:
    raise ImproperlyConfigured(_secret_key_problem)
ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=["localhost"])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    # ArrayField and GinIndex for `Document.addressee_ids` (S 4.9.2); the tsvector field
    # and GIN indexes of search. Part of Django, no new dependency.
    "django.contrib.postgres",
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
    "apps.backup",
    "apps.updates",
    "apps.installer",
    "apps.audit",
]

AUTH_USER_MODEL = "accounts.User"

# Axes first: it refuses a locked-out email + address before the password is checked.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Django's four rules with their own Dutch sentences (apps.accounts.passwords).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "apps.accounts.passwords.UserAttributeSimilarityValidator"},
    {"NAME": "apps.accounts.passwords.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "apps.accounts.passwords.CommonPasswordValidator"},
    {"NAME": "apps.accounts.passwords.NumericPasswordValidator"},
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.errors.ApiCacheControlMiddleware",
    "apps.common.errors.ApiMethodNotAllowedMiddleware",
    "apps.common.errors.ApiContentLengthMiddleware",
    "apps.common.errors.ApiJsonBodyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Django's SessionMiddleware; a session ended during an /api/ request answers 401 JSON.
    "apps.common.errors.ApiSessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # After authentication: signs out a session without a record or past its limits.
    "apps.accounts.middleware.UserSessionMiddleware",
    # The account's own language choice wins over Accept-Language (LocaleMiddleware).
    "apps.accounts.middleware.AccountLanguageMiddleware",
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
    env.db_url_config(_database_url("DATABASE_OWNER_URL"))
    if _database_url("DATABASE_OWNER_URL")
    else None
)
# The read-only back-up role (BYPASSRLS, member of pg_read_all_data). On the host only
# the test run uses it (tests/conftest.py); in the container only the keeper.
DATABASE_BACKUP: dict[str, object] | None = (
    env.db_url_config(_database_url("DATABASE_BACKUP_URL"))
    if _database_url("DATABASE_BACKUP_URL")
    else None
)
# A superuser, only for the one-off container that restores a back-up (it creates roles
# and extensions). Never set on the host or in the serving containers, nor in the tests.
DATABASE_SUPERUSER: dict[str, object] | None = (
    env.db_url_config(_database_url("DATABASE_SUPERUSER_URL"))
    if _database_url("DATABASE_SUPERUSER_URL")
    else None
)
if _use_owner_role and DATABASE_OWNER is None:
    raise ImproperlyConfigured("BRIEVIE_DB_ROLE=owner needs DATABASE_OWNER_URL.")
DATABASES = {
    "default": DATABASE_OWNER
    if _use_owner_role and DATABASE_OWNER
    else env.db_url_config(_database_url("DATABASE_URL") or _secret("DATABASE_URL"))
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
# No JIT compilation, for every connection the app, the migrate run and the tests open.
# PostgreSQL compiles a query once its estimated cost passes jit_above_cost (100,000);
# with the visibility policies inlined into every list query the estimates pass it
# easily, and compiling then costs 0.3-1.3 s for queries that run in well under that
# (measured, DECISIONS 2026-11-20). Set at connection start through libpq's `options`:
# no extra round trip, and the server and database stay at their defaults.
_db_options = DATABASES["default"].get("OPTIONS")
_db_options = dict(_db_options) if isinstance(_db_options, dict) else {}
_db_options["options"] = f"{_db_options.get('options', '')} -c jit=off".strip()
# `init` (apps.installer) freezes the database during an update (apps.installer.freeze):
# only a process that sets this may still write, and only `init` does.
BRIEVIE_INIT_WRITABLE: bool = env.bool("BRIEVIE_INIT_WRITABLE", default=False)
if BRIEVIE_INIT_WRITABLE:
    _db_options["options"] = f"{_db_options['options']} -c default_transaction_read_only=off"
DATABASES["default"]["OPTIONS"] = _db_options

# The database cache is shared by all gunicorn workers (throttle counts).
# `manage.py createcachetable` creates its table; the test run does so itself.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "brievie_cache",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Dutch is the source language and the default; English has a catalogue in locale/
# (apps.common.catalogue, `make messages`). Each language in its own name.
LANGUAGE_CODE = "nl"
LANGUAGES = [("nl", "Nederlands"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
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
# WhiteNoise sends `Access-Control-Allow-Origin: *` by default. The web app loads its files
# from its own origin only, and the iOS app never reads them: no other site gets access.
WHITENOISE_ALLOW_ALL_ORIGINS = False

# The server is reachable from the internet behind Caddy, which terminates TLS.
# Local http development can switch the secure cookies off in deploy/.env.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE: bool = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE: bool = env.bool("CSRF_COOKIE_SECURE", default=True)
SESSION_COOKIE_SAMESITE = "Lax"
# Web sessions (D 7.2): signed out after this many days without use, and after at most
# this many days in all. Enforced by apps.accounts.middleware; part C moves them to the
# server settings page.
BRIEVIE_SESSION_IDLE_DAYS: int = env.int("BRIEVIE_SESSION_IDLE_DAYS", default=14)
BRIEVIE_SESSION_MAX_DAYS: int = env.int("BRIEVIE_SESSION_MAX_DAYS", default=90)
if BRIEVIE_SESSION_IDLE_DAYS < 1 or BRIEVIE_SESSION_MAX_DAYS < 1:
    raise ImproperlyConfigured("BRIEVIE_SESSION_IDLE_DAYS and _MAX_DAYS must be at least 1.")
if BRIEVIE_SESSION_IDLE_DAYS > BRIEVIE_SESSION_MAX_DAYS:
    raise ImproperlyConfigured("BRIEVIE_SESSION_IDLE_DAYS may not exceed BRIEVIE_SESSION_MAX_DAYS.")
SESSION_COOKIE_AGE = BRIEVIE_SESSION_IDLE_DAYS * 24 * 3600

# Server switch for the AI autopilot (a workspace's own switches only count while this is on).
BRIEVIE_AI_AUTOPILOT_ALLOWED: bool = env.bool("BRIEVIE_AI_AUTOPILOT_ALLOWED", default=True)
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
# https://. They go into the pairing QR code and the pairing link. A phone that reaches
# the server through an address that is not listed here and is not covered by the
# server's own certificate authority cannot pair from a link; five failed attempts make
# that code useless and a new one must be made.
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
# Uploads per member of a workspace and per workspace (apps.documents.upload_limits):
# a household scans a pile at once, a mail room more; one member must not fill the
# worker's queue for every workspace. Ninja's rate format: count/period (s, m, h, d).
BRIEVIE_UPLOADS_PER_MEMBER: str = env.str("BRIEVIE_UPLOADS_PER_MEMBER", default="60/10m")
BRIEVIE_UPLOADS_PER_WORKSPACE: str = env.str("BRIEVIE_UPLOADS_PER_WORKSPACE", default="600/1h")
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
BRIEVIE_MASTER_KEY: str = _secret("BRIEVIE_MASTER_KEY", default="")
# The restic binary for back-ups (apps.backup.restic).
BRIEVIE_RESTIC_BINARY: str = env.str("BRIEVIE_RESTIC_BINARY", default="restic")

# Encrypted document files. A volume in Compose; never a path elsewhere in the code.
BRIEVIE_STORAGE_ROOT: str = env.str("BRIEVIE_STORAGE_ROOT", default="/data/documents")

# Back-up destination (apps.backup.destination reads these lazily). The password is
# derived in the container (`manage.py derive_backup_password`) and may be absent on
# a first run. A folder (compose mounts it at BRIEVIE_BACKUP_MOUNT) or a remote
# repository is set, never both.
BRIEVIE_BACKUP_PASSWORD: str = _secret("BRIEVIE_BACKUP_PASSWORD", default="")
BRIEVIE_BACKUP_REPOSITORY: str = env.str("BRIEVIE_BACKUP_REPOSITORY", default="")
BRIEVIE_BACKUP_DIR_SET: bool = env.bool("BRIEVIE_BACKUP_DIR_SET", default=False)
BRIEVIE_BACKUP_MOUNT: str = env.str("BRIEVIE_BACKUP_MOUNT", default="/backup")
# The back-up folder as the host knows it (for the recovery sheet only).
BRIEVIE_BACKUP_DIR_HOST: str = env.str("BRIEVIE_BACKUP_DIR_HOST", default="")
BRIEVIE_BACKUP_APPEND_ONLY: bool = env.bool("BRIEVIE_BACKUP_APPEND_ONLY", default=False)
BRIEVIE_BACKUP_S3_ACCESS_KEY_ID: str = env.str("BRIEVIE_BACKUP_S3_ACCESS_KEY_ID", default="")
BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY: str = _secret(
    "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY", default=""
)
BRIEVIE_BACKUP_REST_USERNAME: str = env.str("BRIEVIE_BACKUP_REST_USERNAME", default="")
BRIEVIE_BACKUP_REST_PASSWORD: str = _secret("BRIEVIE_BACKUP_REST_PASSWORD", default="")
# The hour (server time, 0-23) of the nightly back-up that the keeper makes once a
# destination is set (apps.backup.schedule). At most one every 20 hours, also after a
# back-up made by hand.
BRIEVIE_BACKUP_HOUR: int = env.int("BRIEVIE_BACKUP_HOUR", default=2)
if not 0 <= BRIEVIE_BACKUP_HOUR <= 23:
    raise ImproperlyConfigured("BRIEVIE_BACKUP_HOUR must be between 0 and 23.")
# `init` prepares an empty back-up folder once (apps.installer): only a folder that is empty,
# has no marker and is not on the data disk; never with the same-disk override.
BRIEVIE_BACKUP_AUTO_INIT: bool = env.bool("BRIEVIE_BACKUP_AUTO_INIT", default=False)
BRIEVIE_BACKUP_CONFIG_DIR: str = env.str("BRIEVIE_BACKUP_CONFIG_DIR", default="/data/backup-config")
BRIEVIE_AUTHORITY_COPY_DIR: str = env.str("BRIEVIE_AUTHORITY_COPY_DIR", default="/data/authority")
BRIEVIE_HOSTNAME: str = env.str("BRIEVIE_HOSTNAME", default="")
# True only where the standalone compose file (deploy/compose/docker-compose.yml) says so;
# never set by the image or by `deploy/docker-compose.yml`. The web interface offers the
# recovery sheet only there: the api of a `./brievie` install lacks what the sheet needs.
BRIEVIE_COMPOSE_INSTALL: bool = env.bool("BRIEVIE_COMPOSE_INSTALL", default=False)

# Audit rows older than this are removed every night (apps.audit.jobs). The database
# refuses to remove rows younger than 90 days, so a lower value is a mistake.
BRIEVIE_AUDIT_RETENTION_DAYS: int = env.int("BRIEVIE_AUDIT_RETENTION_DAYS", default=365)
if BRIEVIE_AUDIT_RETENTION_DAYS < 90:
    raise ImproperlyConfigured("BRIEVIE_AUDIT_RETENTION_DAYS must be at least 90.")

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
    # ./brievie sets WARNING for the commands it runs; the services keep INFO.
    "root": {"handlers": ["console"], "level": env.str("BRIEVIE_LOG_LEVEL", default="INFO")},
}
