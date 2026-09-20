import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")


def required_env(name):
    aliases = {"POSTGRES_DB": "PGDATABASE", "POSTGRES_USER": "PGUSER",
               "POSTGRES_PASSWORD": "PGPASSWORD", "POSTGRES_HOST": "PGHOST", "POSTGRES_PORT": "PGPORT"}
    value = os.environ.get(name) or os.environ.get(aliases.get(name, name))
    if not value:
        raise ImproperlyConfigured(f"Set {name} in the environment or the project root .env file.")
    return value


SECRET_KEY = required_env("DJANGO_SECRET_KEY")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,192.168.3.104").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts.apps.AccountsConfig",
    "apps.customers.apps.CustomersConfig",
    "apps.services.apps.ServicesConfig",
    "apps.leads.apps.LeadsConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.workflows.apps.WorkflowsConfig",
    "apps.activity.apps.ActivityConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
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
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required_env("POSTGRES_DB"),
        "USER": required_env("POSTGRES_USER"),
        "PASSWORD": required_env("POSTGRES_PASSWORD"),
        "HOST": required_env("POSTGRES_HOST"),
        "PORT": required_env("POSTGRES_PORT"),
    },
}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}
LANGUAGE_CODE = "ru"
TIME_ZONE = "Asia/Qyzylorda"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


STATICFILES_DIRS = [BASE_DIR.parent / "frontend" / "dist"]

AUTHENTICATION_BACKENDS = ["apps.accounts.backends.RoleBackend"]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"telegram": {"format": "{asctime} {levelname} {message}", "style": "{"}},
    "handlers": {
        "telegram_console": {"class": "logging.StreamHandler", "formatter": "telegram"},
        "telegram_file": {"class": "logging.handlers.RotatingFileHandler", "filename": BASE_DIR.parent / "telegram.log", "maxBytes": 2_000_000, "backupCount": 3, "encoding": "utf-8", "formatter": "telegram"},
    },
    "loggers": {"crm.telegram": {"handlers": ["telegram_console", "telegram_file"], "level": "INFO", "propagate": False}},
}


# Railway terminates TLS; only enable these settings behind its trusted proxy.
if os.environ.get("DJANGO_PRODUCTION", "").lower() == "true":
    DEBUG = False
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    configured_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", railway_domain)
    ALLOWED_HOSTS = [host.strip() for host in configured_hosts.split(",") if host.strip()]
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS to the public Railway domain.")
    CSRF_TRUSTED_ORIGINS = ["https://" + host for host in ALLOWED_HOSTS]
    # Railway probes this host over HTTP before routing public traffic.
    if "healthcheck.railway.app" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append("healthcheck.railway.app")
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    }
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "crm_cache"}}
    LOGGING["loggers"]["crm.telegram"]["handlers"] = ["telegram_console"]
    LOGGING["handlers"].pop("telegram_file", None)

APPOINTMENT_REMINDER_MINUTES = 60
