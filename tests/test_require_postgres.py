"""REQUIRE_POSTGRES=1 transforma o pulo dos testes `postgres` em falha."""

from pathlib import Path

from tests import conftest


def test_require_postgres_transforma_pulo_em_falha(monkeypatch):
    monkeypatch.delenv("REQUIRE_POSTGRES", raising=False)
    assert conftest.acao_se_indisponivel() == "skip"

    for valor in ("0", "", "false"):
        monkeypatch.setenv("REQUIRE_POSTGRES", valor)
        assert conftest.acao_se_indisponivel() == "skip"

    monkeypatch.setenv("REQUIRE_POSTGRES", "1")
    assert conftest.acao_se_indisponivel() == "fail"


RAIZ = Path(__file__).resolve().parents[1]


def _rodar_interno(pytester, monkeypatch, exigir: bool):
    """Roda um teste `postgres` mínimo, com a lógica real de tests/conftest.py, sem banco."""
    monkeypatch.setenv("PYTHONPATH", str(RAIZ))
    monkeypatch.setenv("DATABASE_URL", "postgres://x:x@127.0.0.1:1/x")
    monkeypatch.setenv("DATABASE_URL_APP", "postgres://x:x@127.0.0.1:1/x")
    if exigir:
        monkeypatch.setenv("REQUIRE_POSTGRES", "1")
    else:
        monkeypatch.delenv("REQUIRE_POSTGRES", raising=False)
    pytester.makepyfile(
        "import pytest\n\n@pytest.mark.postgres\ndef test_precisa_de_banco():\n    pass\n"
    )
    return pytester.runpytest_subprocess("-p", "tests.conftest", "-p", "no:cacheprovider")


def test_require_postgres_com_banco_inacessivel_erra_de_ponta_a_ponta(pytester, monkeypatch):
    resultado = _rodar_interno(pytester, monkeypatch, exigir=True)
    resultado.assert_outcomes(errors=1)
    resultado.stdout.fnmatch_lines(["*REQUIRE_POSTGRES=1 e o banco não está disponível*"])


def test_sem_require_postgres_banco_inacessivel_pula(pytester, monkeypatch):
    resultado = _rodar_interno(pytester, monkeypatch, exigir=False)
    resultado.assert_outcomes(skipped=1)
