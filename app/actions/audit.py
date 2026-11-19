"""Registro de auditoria em memória. A persistência (tabela ActionLog append-only, com
papéis do PostgreSQL, triggers e cadeia de hashes) entra na Fase 2 (design, seção 5.8).

Uma entrada nunca contém o conteúdo da ação: só o hash do payload.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuditEntry:
    event: str  # created | callback | expired | superseded | authorization | executed
    action: str
    target: str
    approval_id: str | None
    payload_hash: str
    # recorded | approved | rejected | denied | ignored | expired | allowed | ok | error
    decision: str
    reason: str
    at: datetime
