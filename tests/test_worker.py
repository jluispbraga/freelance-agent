"""Worker real contra o PostgreSQL e o subcomando `worker-health` (healthcheck do container)."""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import dj_database_url
import procrastinate
import psycopg
import pytest
from django.db import connection

from app.main import main

RAIZ = Path(__file__).resolve().parents[1]

# Processo filho: registra tarefas SÓ deste teste no app do Procrastinate e roda o alvo `worker`
# de verdade (python -m app.main worker), conectado como fa_app.
_FILHO = """
import sys

import django

django.setup()
from procrastinate.contrib.django import app


@app.task(name="teste.tarefa_default", queue="default")
def tarefa_default():
    return None


@app.task(name="teste.tarefa_llm", queue="llm")
def tarefa_llm():
    return None


from app.main import main

sys.exit(main(["worker"]))
"""


def _url_app(nome_banco: str) -> str:
    config = dj_database_url.parse(os.environ["DATABASE_URL_APP"])
    return (
        f"postgres://{config['USER']}:{config['PASSWORD']}@{config['HOST']}:"
        f"{config['PORT'] or 5432}/{nome_banco}"
    )


_TAREFAS = ("teste.tarefa_default", "teste.tarefa_llm")


def _estados(url: str) -> dict[str, str]:
    with psycopg.connect(url, autocommit=True) as conn:
        linhas = conn.execute(
            "SELECT task_name, status::text FROM procrastinate_jobs WHERE task_name = ANY(%s)",
            [list(_TAREFAS)],
        ).fetchall()
    return dict(linhas)


def _apagar_jobs(url: str) -> None:
    # As tabelas do Procrastinate não são limpas pelo flush do pytest-django.
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM procrastinate_jobs WHERE task_name = ANY(%s)", [list(_TAREFAS)])


def _esperar(condicao, prazo: float, processo: subprocess.Popen) -> bool:
    limite = time.monotonic() + prazo
    while time.monotonic() < limite:
        if condicao():
            return True
        if processo.poll() is not None:
            return condicao()
        time.sleep(0.2)
    return False


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_run_workers_real_processa_jobs_nas_duas_filas(tmp_path):
    url = _url_app(connection.settings_dict["NAME"])
    _apagar_jobs(url)
    try:
        _rodar_worker_real(url, tmp_path / "worker.log")
    finally:
        _apagar_jobs(url)


def _rodar_worker_real(url: str, log: Path) -> None:
    enfileirador = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    with enfileirador.open():
        for tarefa, fila in zip(_TAREFAS, ("default", "llm"), strict=True):
            enfileirador.configure_task(tarefa, queue=fila).defer()
    assert _estados(url) == {"teste.tarefa_default": "todo", "teste.tarefa_llm": "todo"}

    ambiente = {**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(RAIZ)}
    with log.open("w") as saida_filho:
        processo = subprocess.Popen(
            [sys.executable, "-c", _FILHO],
            cwd=RAIZ,
            env=ambiente,
            stdout=saida_filho,
            stderr=subprocess.STDOUT,
        )
    try:
        concluidos = _esperar(
            lambda: set(_estados(url).values()) == {"succeeded"}, prazo=20, processo=processo
        )
        # O worker continua de pé depois de processar (não encerra sozinho).
        time.sleep(3)
        ainda_vivo = processo.poll() is None
        processo.send_signal(signal.SIGTERM)
        codigo = processo.wait(timeout=15)
    finally:
        if processo.poll() is None:
            processo.kill()
            processo.wait()
    saida = log.read_text()

    assert concluidos, f"jobs não concluídos: {_estados(url)}\n{saida}"
    assert ainda_vivo, f"o worker encerrou sem sinal de parada:\n{saida}"
    assert codigo == 0, saida
    # Saída limpa: os workers se desregistraram.
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM procrastinate_workers").fetchone() == (0,)


def _inserir_workers(*idades_em_segundos: int) -> None:
    with contextlib.closing(connection.cursor()) as cursor:
        for idade in idades_em_segundos:
            cursor.execute(
                "INSERT INTO procrastinate_workers (last_heartbeat) "
                "VALUES (now() - make_interval(secs => %s))",
                [idade],
            )


@pytest.mark.postgres
@pytest.mark.django_db
def test_worker_health_ok_com_heartbeats_recentes():
    _inserir_workers(0, 50)

    assert main(["worker-health"]) == 0


@pytest.mark.postgres
@pytest.mark.django_db
def test_worker_health_falha_sem_heartbeat():
    assert main(["worker-health"]) == 1  # nenhum worker registrado

    _inserir_workers(0)
    assert main(["worker-health"]) == 1  # só um dos dois workers

    _inserir_workers(120, 61)
    assert main(["worker-health"]) == 1  # os outros têm heartbeat antigo (> 60 s)

    _inserir_workers(5)
    assert main(["worker-health"]) == 0
