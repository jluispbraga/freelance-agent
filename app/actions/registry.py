"""Registro de ações externas: a política do AUTHORIZATION.md em código.

Regras:
- É a única fonte de níveis de autorização em tempo de execução.
- É imutável: nenhuma variável de ambiente, Admin, banco ou prompt altera um nível.
- Mudar um nível exige mudar o AUTHORIZATION.md e este arquivo no mesmo commit; o teste
  AZ-01 falha se os dois divergirem.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Level(StrEnum):
    """Níveis do AUTHORIZATION.md. O valor é o texto exato usado no documento."""

    AUTO = "Automática"
    AUTO_SANDBOXED = "Automática, em ambiente isolado"
    APPROVAL = "Minha aprovação"
    HUMAN_ONLY = "Eu"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    level: Level
    # Linhas do AUTHORIZATION.md que esta ação implementa (coluna "Ação" da tabela
    # principal ou coluna "Identificador" da tabela de infraestrutura).
    authorization_rows: tuple[str, ...]
    # A ação pode ser executada no escopo implementado hoje? (lista fechada: teste AZ-17)
    has_executor: bool
    # Chaves aceitas no payload; None = sem restrição de chaves.
    allowed_payload_keys: frozenset[str] | None = None


# Prefixos que pertencem ao nível "Eu" mesmo que a ação não esteja registrada.
HUMAN_ONLY_PREFIXES: tuple[str, ...] = ("payment.",)

_SPECS: tuple[ActionSpec, ...] = (
    # --- Módulo 1 (Fases 1 a 4, só entrada manual): únicas ações com executor ---------
    ActionSpec(
        "llm.evaluate",
        Level.AUTO,
        ("Analisar oportunidades", "Classificar projetos", "Calcular preço"),
        has_executor=True,
    ),
    # Infraestrutura (aprovada pelo freelancer em 18/11/2026).
    ActionSpec("owner.notify", Level.AUTO, ("owner.notify",), has_executor=True),
    # --- Conectores: registrados, SEM executor até entrarem no escopo (Fases 5/6) -----
    # Habilitar exige aprovação explícita do freelancer e retenção reavaliada (D4).
    ActionSpec("platform.search", Level.AUTO, ("Analisar oportunidades",), has_executor=False),
    # Infraestrutura (aprovada pelo freelancer em 18/11/2026).
    ActionSpec(
        "platform.oauth_refresh",
        Level.AUTO,
        ("platform.oauth_refresh",),
        has_executor=False,
        allowed_payload_keys=frozenset({"provider", "requested_scopes"}),
    ),
    # --- Módulos futuros: registradas, sem executor --------------------------------
    ActionSpec("proposal.write", Level.AUTO, ("Escrever propostas",), has_executor=False),
    ActionSpec("proposal.submit", Level.APPROVAL, ("Enviar proposta",), has_executor=False),
    ActionSpec("contract.accept", Level.APPROVAL, ("Aceitar contrato",), has_executor=False),
    ActionSpec(
        "project.develop",
        Level.AUTO_SANDBOXED,
        ("Desenvolver projeto contratado",),
        has_executor=False,
    ),
    ActionSpec("delivery.send", Level.APPROVAL, ("Entregar ao cliente",), has_executor=False),
    # --- Nunca executável ------------------------------------------------------------
    ActionSpec("payment.receive", Level.HUMAN_ONLY, ("Receber pagamentos",), has_executor=False),
)

REGISTRY: MappingProxyType[str, ActionSpec] = MappingProxyType({s.name: s for s in _SPECS})
