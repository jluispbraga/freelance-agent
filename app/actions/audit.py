"""Registro de auditoria em memória. A persistência (tabela ActionLog append-only, com
papéis do PostgreSQL, triggers e cadeia de hashes) entra na Fase 2 (design, seção 5.8; ADR-006).

Uma entrada nunca contém o conteúdo da ação: só o hash do payload.

Os cinco últimos campos (`result`, `error`, `duration_ms`, `correlation_id`, `timeout_ms`) são
opcionais e **aditivos** (ADR-006, Opção A): os valores atuais de `event` e `decision` em memória
não mudam. A camada de persistência traduz para o modelo persistido, em que `decision` é a decisão
de autorização e `result` é o resultado da execução; o nome de uma exceção pertence a `error`, nunca
a `reason`.
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
    # --- campos opcionais (ADR-006): preenchidos pelo gateway em dois passos e pela persistência
    result: str | None = None  # ok | error | unknown
    error: str | None = None  # descrição sanitizada de falha de execução; nunca stack trace
    duration_ms: int | None = None
    correlation_id: str | None = None  # uma por tentativa de execução (ADR-001)
    timeout_ms: int | None = None  # timeout efetivo gravado em M1 (ADR-001)
