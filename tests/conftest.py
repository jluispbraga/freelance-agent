"""Variáveis de ambiente mínimas para os testes que carregam as settings do Django."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.config.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "chave-somente-para-testes")
os.environ.setdefault("DATABASE_URL", "postgres://teste:teste@localhost:5432/teste")

# Os testes `postgres` usam o pytest-django (banco de teste); ele exige o Django já configurado.
import django  # noqa: E402
import pytest  # noqa: E402

django.setup()


def _postgres_disponivel() -> str | None:
    """Devolve o motivo para pular os testes `postgres`, ou None se o banco está acessível."""
    import psycopg

    url_dono = os.environ.get("DATABASE_URL", "")
    url_app = os.environ.get("DATABASE_URL_APP", "")
    if not url_dono.startswith(("postgres://", "postgresql://")):
        return "DATABASE_URL não aponta para um PostgreSQL"
    if not url_app:
        return "DATABASE_URL_APP (conexão como fa_app) não definida"
    try:
        for url in (url_dono, url_app):
            psycopg.connect(url, connect_timeout=2).close()
    except psycopg.Error as erro:
        return f"PostgreSQL inacessível ({type(erro).__name__}); rode scripts/test_db_setup.sh"
    return None


def acao_se_indisponivel() -> str:
    """Com REQUIRE_POSTGRES=1 (CI), banco inacessível falha os testes `postgres`; senão, pula."""
    return "fail" if os.environ.get("REQUIRE_POSTGRES") == "1" else "skip"


_MOTIVO_INDISPONIVEL = pytest.StashKey[str]()


def pytest_collection_modifyitems(config, items):
    marcados = [item for item in items if item.get_closest_marker("postgres")]
    if not marcados:
        return
    motivo = _postgres_disponivel()
    if not motivo:
        return
    if acao_se_indisponivel() == "fail":
        config.stash[_MOTIVO_INDISPONIVEL] = motivo
        return
    for item in marcados:
        item.add_marker(pytest.mark.skip(reason=f"postgres: {motivo}"))


def pytest_runtest_setup(item):
    motivo = item.config.stash.get(_MOTIVO_INDISPONIVEL, None)
    if motivo and item.get_closest_marker("postgres"):
        pytest.fail(f"REQUIRE_POSTGRES=1 e o banco não está disponível: {motivo}", pytrace=False)
