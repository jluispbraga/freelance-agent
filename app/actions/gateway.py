"""Gateway da Central de Ações: único caminho para executar uma ação externa.

Sequência: autorizar → registrar → executar → registrar. Nesta etapa a auditoria é
devolvida em memória; a gravação no ActionLog (append-only) entra na Fase 2.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.actions.approval import ApprovalRecord, content_hash, mark_used
from app.actions.audit import AuditEntry
from app.actions.policy import ActionRequest, Context, Decision, authorize
from app.actions.registry import REGISTRY, ActionSpec, Level

Executor = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class ExecutionResult:
    decision: Decision
    approval: ApprovalRecord | None
    audit: tuple[AuditEntry, ...]
    result: Any = None
    error: str | None = None


def execute(
    request: ActionRequest,
    ctx: Context,
    executor: Executor,
    registry: Mapping[str, ActionSpec] = REGISTRY,
) -> ExecutionResult:
    approval_id = ctx.approval.id if ctx.approval is not None else None
    try:
        payload_hash = content_hash(request.payload)
    except (TypeError, ValueError):
        payload_hash = "nao_serializavel"
        decision = Decision(request.action, False, "payload_nao_serializavel")
    else:
        decision = authorize(request, ctx, registry)

    def entry(event: str, outcome: str, reason: str) -> AuditEntry:
        return AuditEntry(
            event=event,
            action=request.action,
            target=request.target,
            approval_id=approval_id,
            payload_hash=payload_hash,
            decision=outcome,
            reason=reason,
            at=ctx.now,
        )

    authorization = entry(
        "authorization", "allowed" if decision.allowed else "denied", decision.reason
    )
    if not decision.allowed:
        return ExecutionResult(decision, ctx.approval, (authorization,))

    try:
        result = executor(request.payload)
    except Exception as exc:  # o erro é registrado só pelo tipo, sem conteúdo
        error = type(exc).__name__
        return ExecutionResult(
            decision,
            ctx.approval,
            (authorization, entry("executed", "error", error)),
            error=error,
        )

    approval = ctx.approval
    if decision.level is Level.APPROVAL:
        approval = mark_used(approval, ctx.now)
    return ExecutionResult(
        decision, approval, (authorization, entry("executed", "ok", "executado")), result
    )
