"""Verificação arquitetural: nenhum código de produção executa ações fora de `execute_and_record`.

`execute()` e `run_step()` só podem ser usados por `gateway.py` (definição) e por `persistence.py`
(que usa `run_step`). Qualquer outro módulo de `app/` que os importe ou chame reprova.
"""

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "app"
PROIBIDOS = {"execute", "run_step"}
# `persistence.py` é o único consumidor legítimo de `run_step`; ninguém usa `execute`.
PERMITIDOS = {
    "app/actions/gateway.py": {"execute", "run_step"},
    "app/actions/persistence.py": {"run_step"},
}


def _usos_proibidos(caminho: Path, rel: str | None = None) -> list[str]:
    rel = rel or caminho.relative_to(RAIZ).as_posix()
    permitido = PERMITIDOS.get(rel, set())
    achados = []
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and (no.module or "").startswith("app.actions.gateway"):
            achados += [
                f"{rel}:{no.lineno} importa {a.name}"
                for a in no.names
                if a.name in PROIBIDOS - permitido
            ]
        elif isinstance(no, ast.Attribute) and no.attr in PROIBIDOS - permitido:
            if isinstance(no.value, ast.Name) and no.value.id in {"gateway", "persistence"}:
                achados.append(f"{rel}:{no.lineno} usa {no.value.id}.{no.attr}")
        elif isinstance(no, ast.Import):
            achados += [
                f"{rel}:{no.lineno} importa o módulo {a.name} para usá-lo"
                for a in no.names
                if a.name == "app.actions.gateway" and rel != "app/actions/gateway.py"
            ]
    return achados


def test_nenhum_caminho_de_producao_executa_acoes_sem_execute_and_record():
    achados = []
    for arquivo in sorted(APP.rglob("*.py")):
        achados += _usos_proibidos(arquivo)
    assert achados == [], "ações externas só podem passar por execute_and_record:\n" + "\n".join(
        achados
    )


def test_a_varredura_detecta_uso_indevido(tmp_path):
    """A verificação não é vacuosa: um módulo que chama `execute` é flagrado (sem tocar em app/)."""
    ruim = tmp_path / "violacao.py"
    ruim.write_text("from app.actions.gateway import execute\nexecute(None, None, None)\n")
    assert _usos_proibidos(ruim, "app/services/violacao.py") != []
    ok = tmp_path / "ok.py"
    ok.write_text("from app.actions.persistence import execute_and_record\n")
    assert _usos_proibidos(ok, "app/services/ok.py") == []
