"""Política de segurança do docker-compose.yml, conforme a matriz P4 (secrets-matrix.md)."""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "docker-compose.yml"
SERVICOS_PYTHON = ("migrate", "web", "worker", "bot")
TODOS = ("db", *SERVICOS_PYTHON)

# Matriz P4: {serviço: chaves permitidas no env_file}.
_COMUNS = {"TZ", "DJANGO_SECRET_KEY", "PROFILE_MD_PATH", "DATABASE_URL"}
_TELEGRAM = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID", "TELEGRAM_OWNER_USER_ID"}
MATRIZ = {
    "db": {"TZ", "POSTGRES_PASSWORD", "FA_OWNER_PASSWORD", "FA_APP_PASSWORD"},
    "migrate": set(_COMUNS),
    "web": _COMUNS | {"DJANGO_ALLOWED_HOSTS"},
    "worker": _COMUNS
    | _TELEGRAM
    | {
        "ANTHROPIC_API_KEY",
        "EVAL_TRIAGE_MODEL",
        "EVAL_DEEP_MODEL",
        "EVAL_TRIAGE_EFFORT",
        "EVAL_DEEP_EFFORT",
        "EVAL_TRIAGE_MAX_TOKENS",
        "EVAL_DEEP_MAX_TOKENS",
        "LLM_MAX_DESCRIPTION_CHARS",
        "FIELD_ENCRYPTION_KEY",
    },
    "bot": _COMUNS | _TELEGRAM,
}
PROIBIDAS_FORA_DO_WORKER = {
    "ANTHROPIC_API_KEY",
    "FIELD_ENCRYPTION_KEY",
    "LLM_MAX_DESCRIPTION_CHARS",
}
SEGREDOS = ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "ENCRYPTION_KEY", "DATABASE_URL")


@pytest.fixture(scope="module")
def servicos() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _env_files(servico: dict) -> list[str]:
    arquivos = servico.get("env_file", [])
    return [arquivos] if isinstance(arquivos, str) else list(arquivos)


def _chaves(caminho: Path) -> set[str]:
    chaves = set()
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            chaves.add(linha.split("=", 1)[0].strip())
    return chaves


def test_servicos_esperados(servicos):
    assert set(servicos) == set(TODOS)


def test_nenhum_servico_monta_docker_sock(servicos):
    for nome, servico in servicos.items():
        for volume in servico.get("volumes", []):
            texto = volume if isinstance(volume, str) else yaml.safe_dump(volume)
            assert "docker.sock" not in texto, nome


def test_nenhum_servico_privilegiado(servicos):
    for nome, servico in servicos.items():
        assert not servico.get("privileged", False), nome
        assert servico.get("pid") != "host", nome
        assert servico.get("network_mode") != "host", nome


def test_web_publicado_so_em_localhost(servicos):
    portas = servicos["web"]["ports"]
    assert portas
    for porta in portas:
        assert str(porta).startswith("127.0.0.1:"), porta
    for nome, servico in servicos.items():
        if nome != "web":
            assert "ports" not in servico, nome


def test_servicos_rodam_como_nao_root(servicos):
    for nome in SERVICOS_PYTHON:
        usuario = str(servicos[nome].get("user", ""))
        assert usuario, nome
        uid = usuario.split(":")[0]
        assert uid not in ("0", "root"), nome
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    ultimo_user = [x for x in dockerfile.splitlines() if x.startswith("USER ")][-1]
    assert ultimo_user.split()[1] not in ("root", "0")


def test_profile_md_montado_somente_leitura(servicos):
    for nome in SERVICOS_PYTHON:
        assert "./PROFILE.md:/app/PROFILE.md:ro" in servicos[nome]["volumes"], nome
    assert not any("PROFILE.md" in str(v) for v in servicos["db"].get("volumes", []))


def test_cada_servico_so_recebe_segredos_da_matriz(servicos):
    for nome in TODOS:
        arquivos = _env_files(servicos[nome])
        assert arquivos == [f"env/{nome}.env"], nome
        exemplo = RAIZ / "env" / f"{nome}.env.example"
        assert exemplo.exists(), exemplo
        assert _chaves(exemplo) == MATRIZ[nome], nome


def test_bot_sem_anthropic_e_so_migrate_com_fa_owner(servicos):
    assert "ANTHROPIC_API_KEY" not in _chaves(RAIZ / "env" / "bot.env.example")
    for nome in set(TODOS) - {"worker"}:
        assert not (_chaves(RAIZ / "env" / f"{nome}.env.example") & PROIBIDAS_FORA_DO_WORKER), nome


def _usuario_do_banco(nome: str) -> str:
    for linha in (RAIZ / "env" / f"{nome}.env.example").read_text(encoding="utf-8").splitlines():
        if linha.startswith("DATABASE_URL="):
            return urlsplit(linha.split("=", 1)[1].strip()).username or ""
    raise AssertionError(f"DATABASE_URL ausente em {nome}.env.example")


def test_papel_do_banco_por_servico():
    assert _usuario_do_banco("migrate") == "fa_owner"
    for nome in ("web", "worker", "bot"):
        assert _usuario_do_banco(nome) == "fa_app", nome


def test_imagem_do_uv_tem_versao_fixa():
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    referencias = re.findall(r"--from=(\S*astral-sh/uv\S*)", dockerfile)
    assert referencias
    for ref in referencias:
        assert re.fullmatch(r"[^:\s]+:\d+\.\d+\.\d+", ref), ref


def test_segredos_nao_aparecem_em_environment(servicos):
    for nome, servico in servicos.items():
        ambiente = servico.get("environment", {}) or {}
        chaves = (
            ambiente.keys() if isinstance(ambiente, dict) else [e.split("=")[0] for e in ambiente]
        )
        for chave in chaves:
            assert chave == "TZ" or not any(s in chave for s in SEGREDOS), (nome, chave)


def test_ordem_de_inicializacao(servicos):
    assert servicos["migrate"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert servicos["migrate"]["restart"] == "no"
    for nome in ("web", "worker", "bot"):
        dep = servicos[nome]["depends_on"]["migrate"]["condition"]
        assert dep == "service_completed_successfully", nome


def test_apenas_binds_permitidos(servicos):
    permitidos = {"./PROFILE.md:/app/PROFILE.md:ro", "./docker/db/init-roles.sql"}
    for nome, servico in servicos.items():
        for volume in servico.get("volumes", []):
            if volume.startswith(("./", "/", "..")):
                assert volume.split(":")[0] in {p.split(":")[0] for p in permitidos}, (nome, volume)


def test_dockerignore_exclui_segredos():
    linhas = set((RAIZ / ".dockerignore").read_text(encoding="utf-8").split())
    assert {"PROFILE.md", ".env", "env/*.env", ".git", ".superpowers"} <= linhas


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _nome_do_banco(nome: str) -> str:
    for linha in (RAIZ / "env" / f"{nome}.env.example").read_text(encoding="utf-8").splitlines():
        if linha.startswith("DATABASE_URL="):
            return urlsplit(linha.split("=", 1)[1].strip()).path.lstrip("/")
    raise AssertionError(f"DATABASE_URL ausente em {nome}.env.example")


def test_postgres_db_cria_o_banco_de_todas_as_database_url(servicos):
    """Sem POSTGRES_DB a imagem cria só `postgres` e o init-roles.sql roda no banco errado."""
    banco = servicos["db"]["environment"]["POSTGRES_DB"]
    assert banco == "freelance_agent"
    exemplos = sorted((RAIZ / "env").glob("*.env.example"))
    com_url = [e.name.split(".")[0] for e in exemplos if "DATABASE_URL=" in e.read_text()]
    assert set(com_url) == set(SERVICOS_PYTHON)
    for nome in com_url:
        assert _nome_do_banco(nome) == banco, nome


def test_bot_so_sobe_com_profile_bot(servicos):
    assert servicos["bot"]["profiles"] == ["bot"]
    for nome in set(TODOS) - {"bot"}:
        assert "profiles" not in servicos[nome], nome


def test_healthcheck_do_db_usa_tcp_e_o_banco_da_aplicacao(servicos):
    comando = servicos["db"]["healthcheck"]["test"]
    texto = " ".join(comando) if isinstance(comando, list) else comando
    assert "pg_isready" in texto
    assert "-h 127.0.0.1" in texto
    assert "-d freelance_agent" in texto


def test_restart_policy_web_e_worker(servicos):
    for nome in ("web", "worker"):
        assert servicos[nome]["restart"] == "unless-stopped", nome


def test_worker_tem_healthcheck_worker_health(servicos):
    healthcheck = servicos["worker"]["healthcheck"]
    assert healthcheck["test"] == ["CMD", "python", "-m", "app.main", "worker-health"]
    for chave in ("interval", "timeout", "retries", "start_period"):
        assert chave in healthcheck, chave


def _redes(servico: dict) -> list[str]:
    redes = servico.get("networks", [])
    return list(redes) if isinstance(redes, (list, dict)) else []


def test_redes_seguem_o_design(compose):
    """Design §5.2: db e migrate só na rede interna; web publica; worker e bot com saída."""
    servicos, redes = compose["services"], compose.get("networks", {})
    esperado = {
        "db": {"interna"},
        "migrate": {"interna"},
        "web": {"interna", "publica"},
        "worker": {"interna", "saida"},
        "bot": {"interna", "saida"},
    }
    assert {nome: set(_redes(servicos[nome])) for nome in TODOS} == esperado
    assert redes["interna"]["internal"] is True
    for nome in ("publica", "saida"):
        assert not (redes[nome] or {}).get("internal", False), nome


def test_db_e_migrate_so_em_redes_internas(compose):
    redes = compose.get("networks", {})
    for nome in ("db", "migrate"):
        usadas = _redes(compose["services"][nome])
        assert usadas, nome
        for rede in usadas:
            assert (redes.get(rede) or {}).get("internal") is True, (nome, rede)


def test_toda_rede_usada_esta_declarada_e_ninguem_usa_a_default(compose):
    declaradas = set(compose.get("networks", {}))
    for nome, servico in compose["services"].items():
        usadas = _redes(servico)
        assert usadas, f"{nome} cairia na rede default"
        assert "default" not in usadas, nome
        assert set(usadas) <= declaradas, nome
    assert "default" not in declaradas


def test_init_roles_montado_somente_leitura(servicos):
    binds = [v for v in servicos["db"]["volumes"] if "init-roles.sql" in v]
    assert binds == ["./docker/db/init-roles.sql:/docker-entrypoint-initdb.d/init-roles.sql:ro"]
