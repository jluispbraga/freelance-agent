#!/usr/bin/env python3
"""Lista alterações em arquivos protegidos e de segurança controlada.

Uso: python scripts/check_protected_paths.py <base>...<head> [--strict]
(`<base>...<head>`: só o que mudou desde a base comum; `<base>..<head>` também é aceito.)

Sem --strict (modo relatório, usado no CI) sempre sai com 0. Com --strict sai com 1 se algum
arquivo PROTEGIDO foi alterado. SEGURANCA_CONTROLADA só é listada, para revisão.
Esta é a fonte única das duas listas; o .github/CODEOWNERS deve cobrir ambas (há teste).
No CI roda a versão deste script da revisão BASE, para o PR não enfraquecer o próprio portão.
"""

import argparse
import subprocess
import sys
from fnmatch import fnmatchcase

PROTEGIDOS = (
    "AUTHORIZATION.md",
    "PROFILE.md",
    "app/actions/registry.py",
    "tests/actions/test_authorization_invariants.py",
    "tests/actions/test_approval_invariants.py",
    "docs/superpowers/specs/**",
    "docs/reviews/**",
    "docs/superpowers/plans/**",
    "docs/security/**",
)

SEGURANCA_CONTROLADA = (
    "app/actions/policy.py",
    "app/actions/approval.py",
    "app/actions/gateway.py",
    "app/actions/audit.py",
    "app/actions/authorization_md.py",
    "app/migrations/0001_roles.py",
    "docker/db/init-roles.sql",
    "docker-compose.yml",
    "Dockerfile",
    ".github/workflows/**",
    ".github/CODEOWNERS",
    # O próprio portão e o que o configura ou testa.
    "scripts/check_protected_paths.py",
    "pyproject.toml",
    "tests/conftest.py",
    "tests/infra/**",
)


def casa(caminho: str, padrao: str) -> bool:
    """`dir/**` casa qualquer arquivo sob `dir/`; os demais padrões são globs exatos."""
    if padrao.endswith("/**"):
        return caminho.startswith(padrao[:-2])
    return fnmatchcase(caminho, padrao)


def classificar(arquivos: list[str]) -> tuple[list[str], list[str]]:
    protegidos = [a for a in arquivos if any(casa(a, p) for p in PROTEGIDOS)]
    controlados = [a for a in arquivos if any(casa(a, p) for p in SEGURANCA_CONTROLADA)]
    return protegidos, controlados


def arquivos_alterados(intervalo: str) -> list[str]:
    saida = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", intervalo],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [caminho for caminho in saida.split("\0") if caminho]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intervalo", help="<base>...<head>")
    parser.add_argument("--strict", action="store_true", help="sai com 1 se houver PROTEGIDO")
    args = parser.parse_args(argv)

    protegidos, controlados = classificar(arquivos_alterados(args.intervalo))
    print("PROTEGIDOS alterados:")
    print("\n".join(f"  {a}" for a in protegidos) or "  (nenhum)")
    print("SEGURANCA_CONTROLADA alterados (revisão independente):")
    print("\n".join(f"  {a}" for a in controlados) or "  (nenhum)")
    if args.strict and protegidos:
        print("REPROVADO: arquivo protegido alterado.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
