"""Настройки LoskCar Tracker (MVP).

Стек MVP: Django + SQLite + серверные шаблоны + небольшой vanilla JS.
PostgreSQL, worker и HTTPS-прокси подключаются на этапе эксплуатации;
см. docs/ROADMAP.md.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_optional_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Безопасность ---------------------------------------------------------

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-secret-key-change-before-deploy",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Префикс пути приложения за reverse-proxy (например /app). Если не задан,
# приложение работает от корня.
FORCE_SCRIPT_NAME = os.environ.get("DJANGO_FORCE_SCRIPT_NAME") or None
# Префикс для абсолютных STATIC_URL/MEDIA_URL (ниже). Django дополняет
# относительные URL скрипт-префиксом при первом чтении настройки, но
# get_wsgi_application() вызывает setup(set_prefix=False), а WhiteNoise читает
# STATIC_URL ещё при сборке middleware — относительный "static/" кешируется
# как "/static/" и шаблонный {% static %} теряет префикс. Абсолютное значение
# (с явным префиксом или корнем "/") исключает гонку порядка инициализации.
_SCRIPT_PREFIX = (FORCE_SCRIPT_NAME or "").rstrip("/")

# Секрет API заявок с сайта. В DEBUG разрешён предсказуемый ключ для curl,
# в production без явной настройки API отключён (см. tracker/api.py).
LEAD_API_KEY = os.environ.get(
    "LEAD_API_KEY",
    "dev-lead-key-change-me" if DEBUG else "",
)

# --- Приложение -----------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "tracker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise отдаёт собранные static-файлы без nginx (Render/Railway/VPS).
    # В разработке работает как прозрачный passthrough.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.EnforceActiveUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# Django 5.2 включает cached.Loader даже при DEBUG=True, поэтому правки
# шаблонов не видны до перезапуска сервера. В разработке читаем файлы с диска
# на каждый запрос, в production оставляем кеширование.
TEMPLATE_LOADERS = [  # type: ignore[var-annotated]
    "django.template.loaders.filesystem.Loader",
    "django.template.loaders.app_directories.Loader",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": (
                TEMPLATE_LOADERS
                if DEBUG
                else [("django.template.loaders.cached.Loader", TEMPLATE_LOADERS)]
            ),
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tracker.context_processors.tracker_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# MVP работает на SQLite. Для production задайте DB_ENGINE=postgres и
# переменные DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT; модели переносимы.
if os.environ.get("DB_ENGINE") == "postgres":
    _db_name = os.environ.get("DB_NAME", "")
    if not _db_name:
        raise ImproperlyConfigured(
            "DB_ENGINE=postgres требует переменные DB_NAME, DB_USER, "
            "DB_PASSWORD, DB_HOST и DB_PORT."
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _db_name,
            "USER": os.environ.get("DB_USER", ""),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", ""),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "tracker:board"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = f"{_SCRIPT_PREFIX}/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Static-файлы: в production собираются collectstatic и раздаются WhiteNoise
# (сжатие + кеш-заголовки) без отдельного nginx-конфига. Manifest-хранилище
# не используется, чтобы приложение не падало, если collectstatic ещё не
# запускали; кеширование отдаёт WhiteNoise.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedStaticFilesStorage"
        )
    },
}

MEDIA_URL = f"{_SCRIPT_PREFIX}/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Бизнес-настройки MVP -------------------------------------------------

WORKSHOP_CAPACITY = env_int("WORKSHOP_CAPACITY", 2)
ISSUED_VISIBLE_DAYS = env_int("ISSUED_VISIBLE_DAYS", 7)

# Рабочие часы не подтверждены владельцем. Пока они не заданы, календарь
# показывает сутки целиком и честно предупреждает об этом, а не подставляет
# выдуманный график. Задать можно переменными WORKSHOP_OPEN_HOUR=10 и
# WORKSHOP_CLOSE_HOUR=20, когда владелец подтвердит часы.
WORKSHOP_OPEN_HOUR = env_optional_int("WORKSHOP_OPEN_HOUR")
WORKSHOP_CLOSE_HOUR = env_optional_int("WORKSHOP_CLOSE_HOUR")

# Простой ограничитель частоты для webhook заявок (на процесс).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "loskcar-tracker",
    }
}

# Безопасные заголовки. Полноценный HTTPS включается на деплое.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # токен читается JS для fetch-запросов доски
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Production-усиление (включается автоматически при DJANGO_DEBUG=0) ------

if not DEBUG:
    # Cookie только по HTTPS, если deploy идёт за TLS-прокси (VPS + nginx,
    # Render/Railway). На HTTP-контуре эти флаги снимают переменной
    # DJANGO_COOKIES_INSECURE=1 — только для внутренней проверки.
    if not env_bool("DJANGO_COOKIES_INSECURE"):
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True

    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # Платформы PaaS и nginx терминируют TLS: Django видит схему в заголовке.
    if env_bool("DJANGO_TRUST_PROXY"):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT")
