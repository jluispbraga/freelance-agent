"""Fila Procrastinate (integração Django). Sem tarefas de negócio nesta etapa.

A fila `llm` roda com concorrência 1 (uma chamada ao LLM por vez); `default` roda com mais.
Os dois workers vivem no mesmo processo (container `worker`).
"""

import asyncio
import signal
from typing import Any

from django.db import connection
from procrastinate.contrib.django import app, django_connector

__all__ = ["WORKERS", "WorkerEncerrado", "app", "run_workers", "workers_vivos"]

WORKERS: dict[str, dict[str, Any]] = {
    "llm": {"queues": ["llm"], "concurrency": 1},
    "default": {"queues": ["default"], "concurrency": 4},
}


# Heartbeat: o Procrastinate atualiza a cada 10 s; acima de 60 s o worker é considerado parado.
LIMITE_HEARTBEAT_SEGUNDOS = 60
# Na versão instalada (3.11) `procrastinate_workers` tem só `id` e `last_heartbeat` (sem fila),
# então "os dois workers vivos" é contado: pelo menos len(WORKERS) heartbeats recentes.
_CONSULTA_WORKERS_VIVOS = (
    "SELECT count(*) FROM procrastinate_workers "
    "WHERE last_heartbeat >= now() - make_interval(secs => %s)"
)


def workers_vivos(limite_segundos: int = LIMITE_HEARTBEAT_SEGUNDOS) -> bool:
    """True se todos os workers de `WORKERS` têm heartbeat recente. Erros do banco propagam."""
    with connection.cursor() as cursor:
        cursor.execute(_CONSULTA_WORKERS_VIVOS, [limite_segundos])
        (vivos,) = cursor.fetchone()
    return vivos >= len(WORKERS)


class WorkerEncerrado(RuntimeError):
    """Um worker terminou sem ter recebido pedido de parada (SIGINT/SIGTERM)."""


def run_workers(aplicacao: Any = None) -> None:
    """Roda todos os workers de `WORKERS` em paralelo, no mesmo processo.

    Sem `aplicacao`, usa o app do Procrastinate integrado ao Django com o conector de worker
    (psycopg), como o comando oficial `manage.py procrastinate worker`: o conector do Django não
    suporta LISTEN/NOTIFY e, com ele, o worker para sozinho logo após iniciar.

    Um único handler de SIGINT/SIGTERM cancela os dois; o Procrastinate trata o cancelamento de
    `run_worker_async` como parada graciosa (espera os jobs em andamento). Se um worker falha ou
    termina sem pedido de parada, o outro é parado e uma exceção é propagada (saída com código
    diferente de zero, para o container reiniciar).
    """
    if aplicacao is not None:
        asyncio.run(_rodar(aplicacao))
        return
    if isinstance(app.connector, django_connector.DjangoConnector):
        with app.replace_connector(app.connector.get_worker_connector()) as app_worker:
            asyncio.run(_rodar(app_worker))
    else:
        asyncio.run(_rodar(app))


async def _rodar(aplicacao: Any) -> None:
    loop = asyncio.get_running_loop()
    parada_pedida = False
    async with aplicacao.open_async():
        execucoes = [
            asyncio.create_task(
                aplicacao.run_worker_async(name=nome, install_signal_handlers=False, **parametros),
                name=nome,
            )
            for nome, parametros in WORKERS.items()
        ]

        def _cancelar() -> None:
            for execucao in execucoes:
                execucao.cancel()

        def _parar() -> None:
            nonlocal parada_pedida
            parada_pedida = True
            _cancelar()

        sinais = (signal.SIGINT, signal.SIGTERM)
        for sinal in sinais:
            loop.add_signal_handler(sinal, _parar)
        try:
            await asyncio.wait(execucoes, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for sinal in sinais:
                loop.remove_signal_handler(sinal)
            _cancelar()
            resultados = await asyncio.gather(*execucoes, return_exceptions=True)
        for resultado in resultados:
            if isinstance(resultado, Exception):
                raise resultado
        if not parada_pedida:
            encerrados = [
                execucao.get_name()
                for execucao, resultado in zip(execucoes, resultados, strict=True)
                if not isinstance(resultado, BaseException)
            ]
            raise WorkerEncerrado(
                f"worker(s) {', '.join(encerrados)} terminaram sem pedido de parada"
            )
