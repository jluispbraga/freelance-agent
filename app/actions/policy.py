"""Decisão de autorização da Central de Ações (função pura, sem rede nem banco).

O gateway chama `authorize()` antes de qualquer ação externa. A decisão é negada por
padrão: só é permitida quando todas as regras do nível e da ação são satisfeitas.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.actions.approval import verify_for_execution
from app.actions.registry import HUMAN_ONLY_PREFIXES, REGISTRY, ActionSpec, Level

Trigger = Literal["schedule", "owner", "system"]


@dataclass(frozen=True)
class Owner:
    user_id: int
    chat_id: int


@dataclass(frozen=True)
class SourceState:
    compliance: Literal["pending", "approved", "denied"]
    mode: Literal["scheduled", "on_demand"]


@dataclass(frozen=True)
class Context:
    now: datetime
    owner: Owner
    sources: Mapping[str, SourceState] = field(default_factory=dict)
    granted_scopes: Mapping[str, frozenset[str]] = field(default_factory=dict)
    budget_reserved: bool = False
    sandboxed: bool = False
    approval: Any = None  # ApprovalRecord (app.actions.approval) ou None


@dataclass(frozen=True)
class ActionRequest:
    action: str
    target: str
    payload: Mapping[str, Any]
    trigger: Trigger = "system"


@dataclass(frozen=True)
class Decision:
    action: str
    allowed: bool
    reason: str
    level: Level | None = None


def _deny(request: ActionRequest, reason: str, level: Level | None = None) -> Decision:
    return Decision(request.action, False, reason, level)


# --- Regras específicas por ação (devolvem o motivo da negação ou None) ---------------


def _check_owner_notify(request: ActionRequest, ctx: Context) -> str | None:
    if request.payload.get("chat_id") != ctx.owner.chat_id:
        return "destinatario_nao_e_o_dono"
    return None


def _check_oauth_refresh(request: ActionRequest, ctx: Context) -> str | None:
    granted = ctx.granted_scopes.get(request.payload.get("provider"), frozenset())
    requested = frozenset(request.payload.get("requested_scopes", ()))
    if not requested or not requested <= granted:
        return "amplia_permissoes"
    return None


def _check_platform_search(request: ActionRequest, ctx: Context) -> str | None:
    source = ctx.sources.get(request.payload.get("source"))
    if source is None or source.compliance != "approved":
        return "fonte_nao_aprovada"
    if source.mode == "on_demand" and request.trigger != "owner":
        return "fonte_somente_sob_demanda"
    return None


def _check_llm_evaluate(request: ActionRequest, ctx: Context) -> str | None:
    return None if ctx.budget_reserved else "orcamento_nao_reservado"


_ACTION_CHECKS: Mapping[str, Callable[[ActionRequest, Context], str | None]] = {
    "owner.notify": _check_owner_notify,
    "platform.oauth_refresh": _check_oauth_refresh,
    "platform.search": _check_platform_search,
    "llm.evaluate": _check_llm_evaluate,
}


def authorize(
    request: ActionRequest,
    ctx: Context,
    registry: Mapping[str, ActionSpec] = REGISTRY,
) -> Decision:
    spec = registry.get(request.action)

    # Nível "Eu": sempre negado, inclusive para ações não registradas com prefixo reservado.
    if request.action.startswith(HUMAN_ONLY_PREFIXES) or (
        spec is not None and spec.level is Level.HUMAN_ONLY
    ):
        return _deny(request, "nivel_eu", Level.HUMAN_ONLY)

    if spec is None:
        return _deny(request, "acao_desconhecida")

    if spec.level is Level.APPROVAL:
        if ctx.approval is None:
            return _deny(request, "aprovacao_ausente", spec.level)
        reason = verify_for_execution(ctx.approval, request, ctx.now)
        if reason is not None:
            return _deny(request, reason, spec.level)

    if spec.level is Level.AUTO_SANDBOXED and not ctx.sandboxed:
        return _deny(request, "fora_do_sandbox", spec.level)

    if not spec.has_executor:
        return _deny(request, "sem_executor", spec.level)

    if spec.allowed_payload_keys is not None and not set(request.payload) <= (
        spec.allowed_payload_keys
    ):
        return _deny(request, "payload_fora_do_escopo_da_acao", spec.level)

    check = _ACTION_CHECKS.get(request.action)
    if check is not None:
        try:
            reason = check(request, ctx)
        except (TypeError, ValueError, AttributeError):
            return _deny(request, "payload_invalido", spec.level)
        if reason is not None:
            return _deny(request, reason, spec.level)

    return Decision(request.action, True, "permitido", spec.level)
