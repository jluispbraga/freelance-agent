"""ApprovalRequest: aprovação explícita vinculada ao conteúdo exato da ação.

Lógica pura (sem banco e sem Telegram). Implementa os invariantes 1 a 8 do design
(seção 5.6). A persistência e a atomicidade entre transações concorrentes ficam na Fase 2.
"""

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from app.actions.audit import AuditEntry

DEFAULT_TTL = timedelta(hours=24)
DEFAULT_MESSAGE_MAX_CHARS = 3500


class Status(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    action: str
    target: str
    payload: Mapping[str, Any]
    content_hash: str
    status: Status
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by_user_id: int | None = None
    decided_chat_id: int | None = None
    used_at: datetime | None = None
    superseded_by: str | None = None


@dataclass(frozen=True)
class ApprovalMessage:
    text: str
    attachment: bytes | None
    hash_prefix: str


# --- Invariante 1: payload canônico e hash ----------------------------------------------


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _audit(rec: ApprovalRecord, event: str, decision: str, reason: str, at: datetime):
    return AuditEntry(
        event=event,
        action=rec.action,
        target=rec.target,
        approval_id=rec.id,
        payload_hash=rec.content_hash,
        decision=decision,
        reason=reason,
        at=at,
    )


def new_request(
    action: str,
    target: str,
    payload: Mapping[str, Any],
    *,
    now: datetime,
    ttl: timedelta = DEFAULT_TTL,
) -> tuple[ApprovalRecord, AuditEntry]:
    stored = json.loads(canonical_json(payload))
    rec = ApprovalRecord(
        id=str(uuid.uuid4()),
        action=action,
        target=target,
        payload=stored,
        content_hash=content_hash(stored),
        status=Status.PENDING,
        created_at=now,
        expires_at=now + ttl,
    )
    return rec, _audit(rec, "created", "recorded", "pedido_criado", now)


# --- Invariante 2: mensagem com conteúdo integral e prefixo do hash --------------------


def render_approval_message(
    rec: ApprovalRecord, max_chars: int = DEFAULT_MESSAGE_MAX_CHARS
) -> ApprovalMessage:
    prefix = rec.content_hash[:8]
    header = (
        f"Aprovação necessária: {rec.action}\n"
        f"Alvo: {rec.target}\n"
        f"Hash do conteúdo: {prefix}\n"
        f"Expira em: {rec.expires_at.isoformat()}\n\n"
    )
    body = "\n".join(f"{k}: {v}" for k, v in sorted(rec.payload.items()))
    text = header + body
    if len(text) <= max_chars:
        return ApprovalMessage(text=text, attachment=None, hash_prefix=prefix)
    note = f"Conteúdo integral no anexo (SHA-256 começa com {prefix})."
    return ApprovalMessage(
        text=(header + note)[:max_chars],
        attachment=canonical_json(rec.payload),
        hash_prefix=prefix,
    )


# --- Invariantes 3, 5 e 7: decisões no Telegram -----------------------------------------


def apply_callback(
    rec: ApprovalRecord,
    choice: Literal["approve", "reject"],
    *,
    user_id: int,
    chat_id: int,
    chat_type: str,
    owner,
    now: datetime,
) -> tuple[ApprovalRecord, AuditEntry]:
    if user_id != owner.user_id or chat_id != owner.chat_id or chat_type != "private":
        return rec, _audit(rec, "callback", "denied", "decisor_nao_autorizado", now)
    if rec.status is Status.SUPERSEDED:
        return rec, _audit(rec, "callback", "denied", "substituido", now)
    if rec.status in (Status.APPROVED, Status.REJECTED):
        return rec, _audit(rec, "callback", "ignored", "ja_decidido", now)
    if rec.status is Status.EXPIRED or now >= rec.expires_at:
        expired = dataclasses.replace(rec, status=Status.EXPIRED)
        return expired, _audit(expired, "callback", "denied", "expirado", now)

    new_status = Status.APPROVED if choice == "approve" else Status.REJECTED
    decided = dataclasses.replace(
        rec,
        status=new_status,
        decided_at=now,
        decided_by_user_id=user_id,
        decided_chat_id=chat_id,
    )
    return decided, _audit(decided, "callback", new_status.value, choice, now)


def expire_if_due(rec: ApprovalRecord, *, now: datetime):
    if rec.status is Status.PENDING and now >= rec.expires_at:
        expired = dataclasses.replace(rec, status=Status.EXPIRED)
        return expired, _audit(expired, "expired", "expired", "sem_resposta", now)
    return rec, None


def supersede(rec: ApprovalRecord, new_payload: Mapping[str, Any], *, now: datetime):
    if rec.status is not Status.PENDING:
        raise ValueError("só um pedido pendente pode ser editado")
    new, _ = new_request(
        rec.action, rec.target, new_payload, now=now, ttl=rec.expires_at - rec.created_at
    )
    old = dataclasses.replace(rec, status=Status.SUPERSEDED, superseded_by=new.id)
    return old, new, _audit(old, "superseded", "recorded", f"substituido_por:{new.id}", now)


# --- Invariantes 4, 5 e 6: verificação na execução --------------------------------------

_STATUS_REASONS = {
    Status.PENDING: "aprovacao_pendente",
    Status.REJECTED: "aprovacao_rejeitada",
    Status.EXPIRED: "aprovacao_expirada",
    Status.SUPERSEDED: "aprovacao_substituida",
}


def verify_for_execution(rec: ApprovalRecord, request, now: datetime) -> str | None:
    """Devolve o motivo da negação ou None se a aprovação vale para esta execução."""
    if rec.action != request.action or rec.target != request.target:
        return "aprovacao_de_outra_acao"
    if rec.status is not Status.APPROVED:
        return _STATUS_REASONS[rec.status]
    if rec.used_at is not None:
        return "aprovacao_ja_utilizada"
    if now >= rec.expires_at:
        return "aprovacao_expirada"
    if content_hash(rec.payload) != rec.content_hash:
        return "conteudo_divergente"
    if content_hash(request.payload) != rec.content_hash:
        return "conteudo_divergente"
    return None


def mark_used(rec: ApprovalRecord, now: datetime) -> ApprovalRecord:
    if rec.used_at is not None:
        raise ValueError("aprovação já utilizada")
    return dataclasses.replace(rec, used_at=now)
