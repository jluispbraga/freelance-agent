"""Configuração lida do ambiente: obrigatórias, padrões seguros e PROFILE.md."""

import importlib
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

RAIZ = Path(__file__).resolve().parent.parent


def carregar_settings():
    sys.modules.pop("app.config.settings", None)
    return importlib.import_module("app.config.settings")


def test_settings_exige_variaveis_obrigatorias(monkeypatch):
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)

    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        carregar_settings()


def test_debug_desligado_por_padrao(monkeypatch):
    monkeypatch.delenv("DJANGO_DEBUG", raising=False)

    settings = carregar_settings()

    assert settings.DEBUG is False


def test_configuracao_basica(monkeypatch):
    settings = carregar_settings()

    assert "app" in settings.INSTALLED_APPS
    assert settings.TIME_ZONE == "America/Manaus"
    assert settings.USE_TZ is True
    assert settings.LANGUAGE_CODE == "pt-br"


def test_profile_md_ausente_impede_inicializacao(monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILE_MD_PATH", str(tmp_path / "PROFILE.md"))

    with pytest.raises(ImproperlyConfigured, match="PROFILE"):
        carregar_settings()


def test_profile_md_padrao_e_o_da_raiz_do_repositorio(monkeypatch):
    monkeypatch.delenv("PROFILE_MD_PATH", raising=False)

    settings = carregar_settings()

    assert settings.PROFILE_MD_PATH == RAIZ / "PROFILE.md"
