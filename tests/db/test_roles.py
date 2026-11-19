"""Papéis do PostgreSQL (P4): fa_owner migra; fa_app serve a aplicação sem alterar o schema."""

import os
import re
from contextlib import contextmanager
from pathlib import Path

import dj_database_url
import psycopg
import pytest
from django.db import connection

pytestmark = [pytest.mark.postgres, pytest.mark.django_db]


@contextmanager
def _conectar(url: str, nome_banco: str):
    config = dj_database_url.parse(url)
    with psycopg.connect(
        host=config["HOST"],
        port=config["PORT"] or 5432,
        user=config["USER"],
        password=config["PASSWORD"],
        dbname=nome_banco,
        autocommit=True,
    ) as conn:
        yield conn


@pytest.fixture
def banco(db):
    """Nome do banco de teste, já migrado (a migração concede os privilégios padrão)."""
    return connection.settings_dict["NAME"]


@pytest.fixture
def como_dono(banco):
    with _conectar(os.environ["DATABASE_URL"], banco) as conn:
        yield conn


@pytest.fixture
def como_app(banco):
    with _conectar(os.environ["DATABASE_URL_APP"], banco) as conn:
        yield conn


def test_fa_app_nao_e_dono_nem_superusuario(como_app):
    (super_,) = como_app.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname = 'fa_app'"
    ).fetchone()
    assert super_ is False
    assert como_app.execute("SELECT current_user").fetchone() == ("fa_app",)

    donos = {
        dono
        for (dono,) in como_app.execute(
            "SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    assert donos, "a migração deveria ter criado tabelas"
    assert "fa_app" not in donos

    (dono_schema,) = como_app.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'"
    ).fetchone()
    assert dono_schema != "fa_app"
    (dono_banco,) = como_app.execute(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()"
    ).fetchone()
    assert dono_banco != "fa_app"

    # Design 5.8: fa_app não é membro de fa_owner (nem direta nem indiretamente).
    assert (
        como_app.execute(
            "SELECT 1 FROM pg_auth_members m "
            "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
            "WHERE u.rolname = 'fa_app' AND r.rolname = 'fa_owner'"
        ).fetchall()
        == []
    )
    assert como_app.execute("SELECT pg_has_role('fa_app', 'fa_owner', 'MEMBER')").fetchone() == (
        False,
    )


def test_fa_app_sem_createrole_bypassrls(como_app):
    linha = como_app.execute(
        "SELECT rolcreaterole, rolcreatedb, rolbypassrls, rolsuper, rolinherit "
        "FROM pg_roles WHERE rolname = 'fa_app'"
    ).fetchone()
    assert linha == (False, False, False, False, False)


def test_default_privileges_concedem_crud_em_tabela_nova(como_dono, como_app):
    como_dono.execute("CREATE TABLE teste_privilegios (id serial PRIMARY KEY, valor text)")
    try:
        como_app.execute("INSERT INTO teste_privilegios (valor) VALUES ('a')")
        assert como_app.execute("SELECT valor FROM teste_privilegios").fetchall() == [("a",)]
        como_app.execute("UPDATE teste_privilegios SET valor = 'b'")
        assert como_app.execute("SELECT valor FROM teste_privilegios").fetchall() == [("b",)]
        como_app.execute("DELETE FROM teste_privilegios")
        assert como_app.execute("SELECT count(*) FROM teste_privilegios").fetchone() == (0,)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            como_app.execute("CREATE TABLE criada_pelo_app (id int)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            como_app.execute("TRUNCATE teste_privilegios")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            como_app.execute("ALTER TABLE teste_privilegios ADD COLUMN x int")
        # DROP por quem não é dono falha com "must be owner" (também InsufficientPrivilege).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            como_app.execute("DROP TABLE teste_privilegios")
    finally:
        como_dono.execute("DROP TABLE IF EXISTS teste_privilegios")


def test_fa_app_acessa_tabelas_criadas_pelo_migrate(como_app):
    """Tabelas criadas pelo próprio `migrate` já nascem com os privilégios de fa_app."""
    assert como_app.execute("SELECT count(*) FROM django_content_type").fetchone() is not None
    assert como_app.execute("SELECT count(*) FROM auth_user").fetchone() is not None


def test_init_roles_nao_concede_createdb_ao_owner():
    """Guarda o init-roles.sql: em produção fa_owner não tem CREATEDB (o setup de teste concede)."""
    texto = Path("docker/db/init-roles.sql").read_text()
    sem_comentarios = "\n".join(
        linha for linha in texto.splitlines() if not linha.lstrip().startswith(("--", "\\"))
    )
    comandos = [c.strip() for c in sem_comentarios.split(";") if c.strip()]
    criar = {
        nome: c
        for c in comandos
        for nome in ("fa_owner", "fa_app")
        if re.match(rf"CREATE ROLE {nome}\b", c)
    }
    assert set(criar) == {"fa_owner", "fa_app"}
    for nome, comando in criar.items():
        for atributo in ("SUPERUSER", "CREATEDB", "CREATEROLE", "BYPASSRLS"):
            assert f"NO{atributo}" in comando, f"{nome} deve declarar NO{atributo}"
            assert not re.search(rf"(?<!NO){atributo}", comando), f"{nome} concede {atributo}"
    # Nenhum outro comando concede atributos privilegiados.
    for comando in comandos:
        if comando in criar.values():
            continue
        assert not re.search(r"\b(SUPERUSER|CREATEDB|CREATEROLE|BYPASSRLS)\b", comando), comando
