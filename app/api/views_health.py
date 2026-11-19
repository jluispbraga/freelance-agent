"""Healthcheck: reflete o estado real do banco e da fila. Sem autenticação, sem detalhes.

`workers` é informativo: não muda o código HTTP, para o `web` não ficar unhealthy quando só o
worker está fora (o container `worker` tem o próprio healthcheck, `app.main worker-health`).
"""

import logging

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from app import tasks

logger = logging.getLogger(__name__)


def _verificar(nome: str, consulta: str) -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute(consulta)
            cursor.fetchone()
    except DatabaseError as erro:
        # Só o tipo da exceção vai para o log: a mensagem pode conter host ou credenciais.
        logger.error("healthcheck: falha em %s (%s)", nome, type(erro).__name__)
        return "erro"
    return "ok"


def _workers() -> str:
    try:
        vivos = tasks.workers_vivos()
    except DatabaseError as erro:
        logger.error("healthcheck: falha em workers (%s)", type(erro).__name__)
        return "erro"
    return "ok" if vivos else "erro"


@require_GET
def health(request):
    db = _verificar("db", "SELECT 1")
    fila = _verificar("queue", "SELECT 1 FROM procrastinate_jobs LIMIT 1")
    ok = db == "ok" and fila == "ok"
    return JsonResponse(
        {"status": "ok" if ok else "erro", "db": db, "queue": fila, "workers": _workers()},
        status=200 if ok else 503,
    )
