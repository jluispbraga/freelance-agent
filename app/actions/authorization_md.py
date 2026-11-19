"""Leitura das tabelas do AUTHORIZATION.md, usada só para verificar a consistência com o
registro de ações (teste AZ-01). A política em execução vem do registro, nunca do arquivo.
"""

from app.actions.registry import Level


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_authorization_md(text: str) -> dict[str, Level]:
    """Devolve {linha: nível} para toda tabela com colunas "Ação" e "Autorização".

    Na tabela de infraestrutura, a chave é a coluna "Identificador" (sem crases).
    """
    rows: dict[str, Level] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("|"):
            header = _cells(line)
            if "Ação" in header and "Autorização" in header:
                key_col = (
                    header.index("Identificador")
                    if "Identificador" in header
                    else (header.index("Ação"))
                )
                level_col = header.index("Autorização")
                i += 2  # pula o cabeçalho e a linha separadora
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    cells = _cells(lines[i])
                    key = cells[key_col].strip("`")
                    if key in rows:
                        raise ValueError(f"linha duplicada no AUTHORIZATION.md: {key}")
                    rows[key] = Level(cells[level_col])
                    i += 1
                continue
        i += 1
    return rows
