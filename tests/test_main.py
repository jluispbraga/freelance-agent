"""Ponto de entrada único: despacho dos subcomandos (alvos injetados, nada real é executado)."""

import pytest

from app.main import main


def _alvos():
    chamadas = []

    def alvo(nome):
        def _chamar(args):
            chamadas.append((nome, list(args)))
            return 0

        return _chamar

    alvos = {nome: alvo(nome) for nome in ("web", "worker", "bot", "migrate", "manage")}
    return alvos, chamadas


@pytest.mark.parametrize("subcomando", ["web", "worker", "bot", "migrate", "manage"])
def test_main_despacha_subcomandos(subcomando):
    alvos, chamadas = _alvos()

    codigo = main([subcomando, "extra"], alvos=alvos)

    assert codigo == 0
    assert chamadas == [(subcomando, ["extra"])]


def test_main_propaga_codigo_de_saida_do_alvo():
    alvos, _ = _alvos()
    alvos["migrate"] = lambda args: 3

    assert main(["migrate"], alvos=alvos) == 3


def test_subcomando_desconhecido_retorna_erro(capsys):
    alvos, chamadas = _alvos()

    assert main(["inexistente"], alvos=alvos) != 0
    assert main([], alvos=alvos) != 0
    assert chamadas == []
    assert "web" in capsys.readouterr().err


def test_worker_health_e_um_subcomando():
    from app.main import ALVOS, _worker_health

    assert ALVOS["worker-health"] is _worker_health


def test_web_roda_gunicorn_com_app_bind_do_compose_e_sem_control_socket(monkeypatch):
    """Os argumentos reais do comando `web` do compose, interpretados pelo próprio gunicorn."""
    from pathlib import Path

    import gunicorn.app.wsgiapp as wsgiapp
    import yaml

    from app.main import _web

    compose = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    comando = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["web"]["command"]
    assert comando[0] == "web"
    capturado = {}

    def _run():
        aplicacao = wsgiapp.WSGIApplication("%(prog)s [OPTIONS] [APP_MODULE]")
        capturado["cfg"] = aplicacao.cfg
        capturado["app_uri"] = aplicacao.app_uri

    monkeypatch.setattr(wsgiapp, "run", _run)
    monkeypatch.setattr("sys.argv", ["python"])

    assert _web(comando[1:]) == 0

    assert capturado["app_uri"] == "app.config.wsgi:application"
    assert capturado["cfg"].bind == ["0.0.0.0:8000"]
    assert capturado["cfg"].control_socket_disable is True
