"""Base settings for the Manufacturing ERP project."""
import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY when DEBUG is false.")
    SECRET_KEY = secrets.token_urlsafe(50)
allowed_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS")
if not DEBUG and not allowed_hosts:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS when DEBUG is false.")
ALLOWED_HOSTS = [host.strip() for host in (allowed_hosts or "localhost,127.0.0.1").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "catalog",
    "inventory",
    "manufacturing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").strip().lower()
if DB_ENGINE == "postgres":
    required_database_settings = ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT")
    missing_database_settings = [name for name in required_database_settings if not os.environ.get(name)]
    if missing_database_settings:
        missing = ", ".join(missing_database_settings)
        raise ImproperlyConfigured(f"Set all required PostgreSQL settings: {missing}.")
    try:
        database_port = int(os.environ["DB_PORT"])
    except ValueError as exc:
        raise ImproperlyConfigured("DB_PORT must be an integer between 1 and 65535.") from exc
    if not 1 <= database_port <= 65535:
        raise ImproperlyConfigured("DB_PORT must be an integer between 1 and 65535.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ["DB_HOST"],
        "PORT": str(database_port),
        "CONN_MAX_AGE": 60,
    }}
elif DB_ENGINE == "sqlite":
    if not DEBUG:
        raise ImproperlyConfigured("SQLite is for local development and tests; configure DB_ENGINE=postgres for non-debug use.")
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:
    raise ImproperlyConfigured("DB_ENGINE must be either 'sqlite' or 'postgres'.")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية"), ("en", "English")]
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

# Non-debug use assumes HTTPS is configured at the application server or
# reverse proxy. HSTS stays opt-in until the deployed hostname is confirmed.
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
try:
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
except ValueError as exc:
    raise ImproperlyConfigured("DJANGO_SECURE_HSTS_SECONDS must be a non-negative integer.") from exc
if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured("DJANGO_SECURE_HSTS_SECONDS must be a non-negative integer.")

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "inventory:dashboard"
LOGOUT_REDIRECT_URL = "core:home"
