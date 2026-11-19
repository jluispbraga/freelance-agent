"""Ponto de entrada único: `python -m app.main <subcomando> [args...]`.

Subcomandos: web, worker, worker-health, bot, migrate, manage.
"""

import os
import sys
from collections.abc import Callable, Mapping, Sequence

Alvo = Callable[[Sequence[str]], int | None]

SETTINGS_PADRAO = "app.config.settings"


def _preparar_ambiente() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", SETTINGS_PADRAO)


def _web(args: Sequence[str]) -> int:
    from gunicorn.app.wsgiapp import run

    _preparar_ambiente()
    # Sem o control socket do gunicorn 27 (interface de gerência em runtime; não usamos).
    sys.argv = ["gunicorn", "--no-control-socket", *args, "app.config.wsgi:application"]
    run()
    return 0


def _worker(args: Sequence[str]) -> int:
    import django

    _preparar_ambiente()
    django.setup()
    from app import tasks

    tasks.run_workers()
    return 0


def _worker_health(args: Sequence[str]) -> int:
    """Healthcheck do container `worker`: 0 se os dois workers têm heartbeat recente, senão 1."""
    import django

    _preparar_ambiente()
    django.setup()
    from django.db import DatabaseError

    from app import tasks

    try:
        vivos = tasks.workers_vivos()
    except DatabaseError as erro:
        print(f"worker-health: falha no banco ({type(erro).__name__})", file=sys.stderr)
        return 1
    if not vivos:
        print("worker-health: worker sem heartbeat recente", file=sys.stderr)
        return 1
    return 0


def _bot(args: Sequence[str]) -> int:
    print("O bot do Telegram ainda não foi implementado.", file=sys.stderr)
    return 2


def _manage(args: Sequence[str]) -> int:
    from django.core.management import execute_from_command_line

    _preparar_ambiente()
    execute_from_command_line(["manage.py", *args])
    return 0


def _migrate(args: Sequence[str]) -> int:
    return _manage(["migrate", *args])


ALVOS: Mapping[str, Alvo] = {
    "web": _web,
    "worker": _worker,
    "worker-health": _worker_health,
    "bot": _bot,
    "migrate": _migrate,
    "manage": _manage,
}


def main(argv: Sequence[str] | None = None, alvos: Mapping[str, Alvo] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    alvos = ALVOS if alvos is None else alvos
    if not argv or argv[0] not in alvos:
        validos = "|".join(alvos)
        print(f"Uso: python -m app.main <{validos}> [argumentos...]", file=sys.stderr)
        return 2
    resultado = alvos[argv[0]](argv[1:])
    return 0 if resultado is None else int(resultado)


if __name__ == "__main__":
    sys.exit(main())
