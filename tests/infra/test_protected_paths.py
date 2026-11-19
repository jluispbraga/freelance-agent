"""Guarda de arquivos protegidos: script de CI e cobertura do CODEOWNERS."""

import importlib.util
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "check_protected_paths.py"
CODEOWNERS = RAIZ / ".github" / "CODEOWNERS"


def _carregar_script():
    spec = importlib.util.spec_from_file_location("check_protected_paths", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo_com_alteracao(tmp_path: Path, arquivo: str) -> str:
    """Cria um repositório com dois commits; o segundo altera `arquivo`. Devolve 'base..head'."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "LEIAME.txt").write_text("a")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    alvo = tmp_path / arquivo
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("alterado")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "mudança")
    return f"{base}..{_git(tmp_path, 'rev-parse', 'HEAD')}"


def _rodar(repo: Path, intervalo: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), intervalo, *extra],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_check_protected_paths_reprova_alteracao_em_arquivo_protegido(tmp_path):
    intervalo = _repo_com_alteracao(tmp_path, "AUTHORIZATION.md")
    estrito = _rodar(tmp_path, intervalo, "--strict")
    assert estrito.returncode == 1
    assert "AUTHORIZATION.md" in estrito.stdout
    # Glob de diretório também é protegido.
    outro = tmp_path / "docs" / "security" / "x.md"
    outro.parent.mkdir(parents=True)
    outro.write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "docs")
    base = intervalo.split("..")[0]
    assert "docs/security/x.md" in _rodar(tmp_path, f"{base}..HEAD", "--strict").stdout
    # Modo relatório nunca reprova.
    assert _rodar(tmp_path, intervalo).returncode == 0


def test_check_protected_paths_aceita_alteracao_comum(tmp_path):
    intervalo = _repo_com_alteracao(tmp_path, "app/services/algo.py")
    resultado = _rodar(tmp_path, intervalo, "--strict")
    assert resultado.returncode == 0
    assert "app/services/algo.py" not in resultado.stdout


def test_seguranca_controlada_e_listada_mas_nao_reprova(tmp_path):
    intervalo = _repo_com_alteracao(tmp_path, "docker-compose.yml")
    resultado = _rodar(tmp_path, intervalo, "--strict")
    assert resultado.returncode == 0
    assert "docker-compose.yml" in resultado.stdout


def test_codeowners_cobre_todos_os_arquivos_protegidos_e_de_seguranca():
    modulo = _carregar_script()
    texto = CODEOWNERS.read_text(encoding="utf-8")
    linhas = [
        linha.split()
        for linha in texto.splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    ]
    padroes = {partes[0].lstrip("/") for partes in linhas}
    for padrao in (*modulo.PROTEGIDOS, *modulo.SEGURANCA_CONTROLADA):
        assert padrao in padroes, f"{padrao} sem dono no CODEOWNERS"
    assert all(partes[1:] == ["@OWNER_GITHUB_USERNAME"] for partes in linhas)


def _repo_base(tmp_path: Path, arquivo: str) -> None:
    _git(tmp_path, "init", "-q")
    alvo = tmp_path / arquivo
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("conteúdo original suficientemente longo para a detecção de renomeação\n" * 5)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")


def _intervalo_apos(tmp_path: Path, mensagem: str = "mudança") -> str:
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", mensagem)
    return f"{base}..HEAD"


def test_renomear_arquivo_protegido_reprova_e_lista_o_caminho_antigo(tmp_path):
    _repo_base(tmp_path, "AUTHORIZATION.md")
    _git(tmp_path, "mv", "AUTHORIZATION.md", "x.md")
    resultado = _rodar(tmp_path, _intervalo_apos(tmp_path), "--strict")
    assert resultado.returncode == 1
    assert "AUTHORIZATION.md" in resultado.stdout


def test_renomear_arquivo_de_seguranca_controlada_e_listado(tmp_path):
    _repo_base(tmp_path, "docker-compose.yml")
    _git(tmp_path, "mv", "docker-compose.yml", "outro.yml")
    resultado = _rodar(tmp_path, _intervalo_apos(tmp_path), "--strict")
    assert resultado.returncode == 0
    assert "docker-compose.yml" in resultado.stdout


def test_remover_arquivo_protegido_reprova(tmp_path):
    _repo_base(tmp_path, "PROFILE.md")
    _git(tmp_path, "rm", "-q", "PROFILE.md")
    resultado = _rodar(tmp_path, _intervalo_apos(tmp_path), "--strict")
    assert resultado.returncode == 1
    assert "PROFILE.md" in resultado.stdout


def test_caminho_protegido_com_caractere_nao_ascii_e_detectado(tmp_path):
    _repo_base(tmp_path, "docs/reviews/revisão.md")
    (tmp_path / "docs/reviews/revisão.md").write_text("alterado")
    resultado = _rodar(tmp_path, _intervalo_apos(tmp_path), "--strict")
    assert resultado.returncode == 1
    assert "docs/reviews/revisão.md" in resultado.stdout


def test_o_proprio_portao_e_seus_testes_sao_seguranca_controlada():
    """Quem altera o portão (ou o que o configura/testa) também é listado para revisão."""
    modulo = _carregar_script()
    for padrao in (
        "scripts/check_protected_paths.py",
        "pyproject.toml",
        "tests/conftest.py",
        "tests/infra/**",
    ):
        assert padrao in modulo.SEGURANCA_CONTROLADA, padrao
    _, controlados = modulo.classificar(
        ["scripts/check_protected_paths.py", "pyproject.toml", "tests/infra/test_x.py"]
    )
    assert len(controlados) == 3


def test_persistencia_e_modelo_do_actionlog_sao_seguranca_controlada():
    """Quem registra e armazena as decisões de autorização está sob o mesmo controle do gateway."""
    modulo = _carregar_script()
    protegidos, controlados = modulo.classificar(
        ["app/actions/persistence.py", "app/models/actions.py", "app/services/algo.py"]
    )
    assert protegidos == []
    assert controlados == ["app/actions/persistence.py", "app/models/actions.py"]


def test_intervalo_com_tres_pontos_usa_a_base_comum(tmp_path):
    """`base...head` lista só o que o PR mudou, não o que entrou na base depois."""
    _repo_base(tmp_path, "LEIAME.txt")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "docker-compose.yml").write_text("pr")
    _intervalo_apos(tmp_path)
    _git(tmp_path, "checkout", "-q", base)
    (tmp_path / "AUTHORIZATION.md").write_text("só na base")
    _intervalo_apos(tmp_path, "base avançou")
    base_nova = _git(tmp_path, "rev-parse", "HEAD")

    resultado = _rodar(tmp_path, f"{base_nova}...pr", "--strict")

    assert resultado.returncode == 0
    assert "docker-compose.yml" in resultado.stdout
    assert "AUTHORIZATION.md" not in resultado.stdout
