"""Garante que o CI usa PostgreSQL 17 (o ambiente local pode ser mais antigo)."""

import os

import psycopg
import pytest

pytestmark = pytest.mark.postgres


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="PostgreSQL 17 garantido no CI")
def test_servidor_e_postgres_17_no_ci():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conexao:
        versao = conexao.execute("SHOW server_version_num").fetchone()[0]
    assert int(versao) >= 170000
