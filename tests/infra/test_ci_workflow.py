"""Política do workflow de CI (.github/workflows/ci.yml)."""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _carregar() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_gatilhos_e_permissoes_minimas():
    wf = _carregar()
    assert set(wf[True]) == {"push", "pull_request"}  # PyYAML lê `on` como True
    assert wf["permissions"] == {"contents": "read"}


def test_workflow_fixa_actions_por_sha():
    usos = re.findall(r"uses:\s*(\S+)", WORKFLOW.read_text(encoding="utf-8"))
    assert usos
    for uso in usos:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", uso), f"{uso} sem SHA de commit"


def test_workflow_roda_o_pipeline_na_ordem_com_postgres_17():
    job = _carregar()["jobs"]["test"]
    assert job["services"]["postgres"]["image"] == "postgres:17"
    assert "pg_isready" in job["services"]["postgres"]["options"]
    assert job["env"]["REQUIRE_POSTGRES"] == "1"
    comandos = [p["run"] for p in job["steps"] if "run" in p]
    esperado = [
        "uv sync --frozen",
        "ruff check",
        "ruff format --check",
        "lint-imports",
        "scripts/test_db_setup.sh",
        "pytest",
    ]
    posicoes = [next(i for i, c in enumerate(comandos) if e in c) for e in esperado]
    assert posicoes == sorted(posicoes)


def test_relatorio_de_protegidos_roda_o_script_da_base_com_tres_pontos():
    """O PR não pode enfraquecer o portão que o verifica: o script vem da revisão base."""
    passos = _carregar()["jobs"]["test"]["steps"]
    passo = next(p for p in passos if "check_protected_paths.py" in p.get("run", ""))
    assert "always()" in passo["if"]
    assert "pull_request" in passo["if"]
    comando = " ".join(passo["run"].split())
    base = "${{ github.event.pull_request.base.sha }}"
    head = "${{ github.event.pull_request.head.sha }}"
    ambiente = passo.get("env", {})
    # Os SHAs entram por variáveis de ambiente (sem interpolar expressões no shell).
    assert ambiente.get("BASE_SHA") == base
    assert ambiente.get("HEAD_SHA") == head
    assert 'git show "${BASE_SHA}:scripts/check_protected_paths.py"' in comando
    assert "| python3 -" in comando
    assert '"${BASE_SHA}...${HEAD_SHA}"' in comando
    assert "python3 scripts/check_protected_paths.py" not in comando
