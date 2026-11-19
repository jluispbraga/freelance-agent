"""Configuração do Django, lida do ambiente. Sem segredos no código; DEBUG desligado por padrão."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        raise ImproperlyConfigured(f"Variável de ambiente obrigatória ausente: {nome}")
    return valor


def _booleana(nome: str, padrao: bool = False) -> bool:
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "yes", "on"}


def _lista(nome: str, padrao: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(nome, padrao).split(",") if item.strip()]


SECRET_KEY = _obrigatoria("DJANGO_SECRET_KEY")
DEBUG = _booleana("DJANGO_DEBUG", False)
ALLOWED_HOSTS = _lista("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")

# O PROFILE.md é montado somente leitura; sem ele a aplicação não inicializa.
# (A validação das seções obrigatórias fica na Tarefa 2.3.)
PROFILE_MD_PATH = Path(os.environ.get("PROFILE_MD_PATH") or BASE_DIR / "PROFILE.md")
if not PROFILE_MD_PATH.is_file():
    raise ImproperlyConfigured(f"PROFILE.md não encontrado em {PROFILE_MD_PATH}")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "procrastinate.contrib.django",
    "app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "app.config.urls"
WSGI_APPLICATION = "app.config.wsgi.application"

DATABASES = {"default": dj_database_url.parse(_obrigatoria("DATABASE_URL"))}
# Papel do banco de web, worker e bot (privilégios em app/migrations/0001_roles.py).
DB_APP_ROLE = os.environ.get("DB_APP_ROLE", "fa_app")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Manaus"
USE_I18N = True
USE_TZ = True
