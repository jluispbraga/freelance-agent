"""Fila Procrastinate (concorrência por fila) e healthcheck `/api/health/`."""

import asyncio
import contextlib
import json
import os
import signal
from contextlib import asynccontextmanager

import dj_database_url
import procrastinate
import pytest
from django.db import OperationalError, ProgrammingError, connection
from django.db.migrations.loader import MigrationLoader
from django.test import RequestFactory

from app import tasks
from app.api.views_health import health


def _corpo(resposta) -> dict:
    return json.loads(resposta.content)


@pytest.mark.postgres
@pytest.mark.django_db
def test_health_ok_com_banco_e_fila():
    resposta = health(RequestFactory().get("/api/health/"))

    assert resposta.status_code == 200
    # Nenhum worker rodando no banco de teste: `workers` é só informativo, não muda o HTTP.
    assert _corpo(resposta) == {"status": "ok", "db": "ok", "queue": "ok", "workers": "erro"}


def test_health_503_sem_banco(monkeypatch):
    def _falha(*args, **kwargs):
        raise OperationalError("senha=segredo host=interno")

    monkeypatch.setattr(connection, "cursor", _falha)

    resposta = health(RequestFactory().get("/api/health/"))

    assert resposta.status_code == 503
    assert _corpo(resposta) == {"status": "erro", "db": "erro", "queue": "erro", "workers": "erro"}
    assert b"segredo" not in resposta.content


class _CursorSemTabelasDaFila:
    """Cursor falso: `SELECT 1` funciona; consultas às tabelas do Procrastinate falham."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, consulta, parametros=None):
        if "procrastinate_" in consulta:
            raise ProgrammingError('relation "procrastinate_jobs" does not exist')

    def fetchone(self):
        return (1,)


def test_health_db_ok_fila_erro(monkeypatch):
    monkeypatch.setattr(connection, "cursor", lambda: _CursorSemTabelasDaFila())

    resposta = health(RequestFactory().get("/api/health/"))

    assert resposta.status_code == 503
    assert _corpo(resposta) == {"status": "erro", "db": "ok", "queue": "erro", "workers": "erro"}


class _CursorComWorkers(_CursorSemTabelasDaFila):
    def __init__(self, vivos):
        self.vivos = vivos

    def execute(self, consulta, parametros=None):
        self.consulta = consulta

    def fetchone(self):
        return (self.vivos,) if "procrastinate_workers" in self.consulta else (1,)


@pytest.mark.parametrize(("vivos", "esperado"), [(2, "ok"), (1, "erro"), (0, "erro")])
def test_health_workers_e_informativo(monkeypatch, vivos, esperado):
    monkeypatch.setattr(connection, "cursor", lambda: _CursorComWorkers(vivos))

    resposta = health(RequestFactory().get("/api/health/"))

    assert resposta.status_code == 200
    assert _corpo(resposta) == {"status": "ok", "db": "ok", "queue": "ok", "workers": esperado}


def test_health_rota_registrada():
    from django.urls import resolve

    assert resolve("/api/health/").func is health


def test_fila_llm_tem_concorrencia_1():
    assert tasks.WORKERS["llm"]["concurrency"] == 1
    assert tasks.WORKERS["llm"]["queues"] == ["llm"]
    for nome, worker in tasks.WORKERS.items():
        if nome != "llm":
            assert "llm" not in worker["queues"]
    assert tasks.WORKERS["default"] == {"queues": ["default"], "concurrency": 4}


def test_run_workers_inicia_exatamente_os_dois_workers():
    chamadas = []

    class AppFalso:
        @asynccontextmanager
        async def open_async(self):
            chamadas.append("abriu")
            try:
                yield self
            finally:
                chamadas.append("fechou")

        async def run_worker_async(self, **kwargs):
            chamadas.append(kwargs)

    # Workers que retornam sem pedido de parada são falha (ver teste abaixo).
    with pytest.raises(tasks.WorkerEncerrado):
        tasks.run_workers(AppFalso())

    iniciados = [c for c in chamadas if isinstance(c, dict)]
    assert len(iniciados) == 2
    assert {"queues": ["llm"], "concurrency": 1} in [
        {k: c[k] for k in ("queues", "concurrency")} for c in iniciados
    ]
    assert {"queues": ["default"], "concurrency": 4} in [
        {k: c[k] for k in ("queues", "concurrency")} for c in iniciados
    ]
    assert chamadas[0] == "abriu" and chamadas[-1] == "fechou"


class _AppBloqueante:
    """App falso: cada worker bloqueia até ser cancelado (parada graciosa) ou falhar."""

    def __init__(self, falha_em=None, ao_iniciar_todos=None):
        self.falha_em = falha_em
        self.ao_iniciar_todos = ao_iniciar_todos
        self.iniciados = []
        self.parados = []
        self.kwargs = []

    @asynccontextmanager
    async def open_async(self):
        yield self

    async def run_worker_async(self, **kwargs):
        nome = kwargs["name"]
        self.kwargs.append(kwargs)
        self.iniciados.append(nome)
        try:
            if len(self.iniciados) == 2 and self.ao_iniciar_todos:
                self.ao_iniciar_todos()
            if nome == self.falha_em:
                await asyncio.sleep(0.05)
                raise RuntimeError("falha simulada")
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.parados.append(nome)
            raise


def test_worker_que_termina_sem_sinal_e_falha():
    """Um worker que retorna sozinho (sem SIGTERM/SIGINT) é falha: o outro para e há exceção."""

    class AppQueEncerra(_AppBloqueante):
        async def run_worker_async(self, **kwargs):
            if kwargs["name"] == "default":
                self.iniciados.append("default")
                await asyncio.sleep(0.05)
                return None  # p.ex. side task do Procrastinate falhou e o worker parou
            return await super().run_worker_async(**kwargs)

    app_falso = AppQueEncerra()

    with pytest.raises(tasks.WorkerEncerrado, match="default"):
        tasks.run_workers(app_falso)

    assert app_falso.parados == ["llm"]


def test_run_workers_propaga_falha_de_um_worker():
    app_falso = _AppBloqueante(falha_em="llm")

    with pytest.raises(RuntimeError, match="falha simulada"):
        tasks.run_workers(app_falso)

    assert sorted(app_falso.iniciados) == ["default", "llm"]
    assert app_falso.parados == ["default"]


@contextlib.contextmanager
def _rede_de_seguranca():
    """Sem handler instalado o SIGTERM mataria o pytest; trava vira falha de teste."""

    def _falhar(signum, frame):
        raise AssertionError(f"sinal {signum} sem handler de run_workers (ou travado)")

    anteriores = {s: signal.signal(s, _falhar) for s in (signal.SIGTERM, signal.SIGINT)}
    anterior_alarme = signal.signal(signal.SIGALRM, _falhar)
    signal.alarm(5)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, anterior_alarme)
        for s, h in anteriores.items():
            signal.signal(s, h)


def test_run_workers_sigterm_para_os_dois_workers():
    app_falso = _AppBloqueante(
        ao_iniciar_todos=lambda: asyncio.get_running_loop().call_later(
            0.05, os.kill, os.getpid(), signal.SIGTERM
        )
    )

    with _rede_de_seguranca():
        tasks.run_workers(app_falso)

    assert sorted(app_falso.parados) == ["default", "llm"]
    assert all(k["install_signal_handlers"] is False for k in app_falso.kwargs)


def test_alvo_worker_chama_run_workers():
    from app import main as main_mod

    assert main_mod.ALVOS["worker"] is main_mod._worker


def test_worker_de_main_delega_para_run_workers(monkeypatch):
    from app import main as main_mod

    chamadas = []
    monkeypatch.setattr(tasks, "run_workers", lambda: chamadas.append(1))

    assert main_mod._worker([]) == 0
    assert chamadas == [1]


def test_run_before_cobre_todos_os_apps_com_migracoes():
    from django.apps import apps

    loader = MigrationLoader(None, load=True)
    apps_com_migracoes = {
        cfg.label for cfg in apps.get_app_configs() if cfg.label in loader.migrated_apps
    }
    assert "procrastinate" in apps_com_migracoes
    run_before = set(loader.disk_migrations[("app", "0001_roles")].run_before)
    for label in apps_com_migracoes - {"app"}:
        primeira = min(nome for (app_label, nome) in loader.disk_migrations if app_label == label)
        assert (label, primeira) in run_before, label


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_fa_app_consegue_enfileirar_job():
    config = dj_database_url.parse(os.environ["DATABASE_URL_APP"])
    nome_banco = connection.settings_dict["NAME"]
    conninfo = (
        f"host={config['HOST']} port={config['PORT'] or 5432} user={config['USER']} "
        f"password={config['PASSWORD']} dbname={nome_banco}"
    )
    app_teste = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=conninfo))

    @app_teste.task(name="teste.noop", queue="default")
    def noop():
        return None

    with app_teste.open():
        noop.defer()

    with contextlib.closing(connection.cursor()) as cursor:
        cursor.execute(
            "SELECT count(*) FROM procrastinate_jobs WHERE task_name = %s AND queue_name = %s",
            ["teste.noop", "default"],
        )
        assert cursor.fetchone()[0] == 1
