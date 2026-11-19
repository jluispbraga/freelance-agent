"""Gateway da Central de Ações: único caminho para executar uma ação externa.

Sequência: autorizar → registrar → executar → registrar. A execução é dividida em dois passos
públicos (ADR-001), e `execute()` continua sendo a composição dos dois, com a mesma assinatura e o
mesmo comportamento de antes:

- `authorize_step` é **determinística**: não gera identificadores, não acessa banco nem rede. O
  `correlation_id` e o `timeout_ms` são definidos por quem chama (`app/actions/persistence.py`).
- `run_step` **não é pura**: ela chama o executor, que produz efeitos externos. Ela não grava no
  banco, não gera identificadores e não impõe o timeout; isso é responsabilidade da persistência.

**Caminho de produção:** `persistence.execute_and_record`. `execute()` é o caminho legado, sem
timeout nem registro; nenhum código de produção pode chamá-lo (há teste de arquitetura).

**Selo de autorização.** `run_step` só executa um `AuthorizationStep` produzido por
`authorize_step`. O passo carrega um selo HMAC-SHA256 cuja chave é gerada por processo, vive só
dentro de um *closure* deste módulo e nunca sai dele. Congelar a dataclass impede alterar um passo
legítimo, mas não impede **fabricar** um; o selo impede as duas coisas, e vincula o passo ao pedido
(ação, alvo, hash e snapshot do payload), ao contexto (`ctx.now`), ao **estado completo da
aprovação** usada (não só o `id`) e a todos os campos da decisão e da entrada.

**Snapshot do payload.** `authorize_step` lê o payload do chamador **uma única vez**, em JSON
canônico, e dele saem o hash, a avaliação da política e o conteúdo entregue ao executor. O
`dict` do chamador é mutável mesmo dentro de uma `ActionRequest` congelada; por isso o executor
nunca o recebe: `run_step` entrega uma cópia nova do snapshot autorizado.
"""

import dataclasses
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.actions.approval import ApprovalRecord, canonical_json, content_hash, mark_used
from app.actions.audit import AuditEntry
from app.actions.policy import ActionRequest, Context, Decision, authorize
from app.actions.registry import REGISTRY, ActionSpec, Level

Executor = Callable[[Mapping[str, Any]], Any]


class InvalidAuthorizationStep(Exception):
    """O passo recebido não foi produzido por `authorize_step` para este pedido e contexto."""


def _make_sealer() -> tuple[Callable[[Any], str], Callable[[Any, str], bool]]:
    key = secrets.token_bytes(32)  # por processo; só existe dentro deste closure

    def sign(fields: Any) -> str:
        data = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hmac.new(key, data.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(fields: Any, seal: str) -> bool:
        return isinstance(seal, str) and hmac.compare_digest(sign(fields), seal)

    return sign, verify


_sign, _verify = _make_sealer()


@dataclass(frozen=True)
class ExecutionResult:
    decision: Decision
    approval: ApprovalRecord | None
    audit: tuple[AuditEntry, ...]
    result: Any = None
    error: str | None = None


@dataclass(frozen=True)
class AuthorizationStep:
    """Passo de autorização (M1). Só `authorize_step` o produz com um selo válido."""

    decision: Decision
    entry: AuditEntry  # evento "authorization"
    payload_hash: str
    approval_id: str | None
    correlation_id: str | None = None
    timeout_ms: int | None = None
    # Snapshot canônico e imutável (bytes) do payload AUTORIZADO. O executor recebe uma cópia
    # nova obtida daqui, nunca a referência mutável do chamador.
    payload_canonical: bytes = field(default=b"", repr=False, compare=False)
    seal: str = field(default="", repr=False, compare=False)


def _approval_fingerprint(approval: Any) -> Any:
    """Estado COMPLETO da aprovação usada na autorização, não só o `id`.

    `Context` e `ApprovalRecord` são `frozen`, mas `Context.approval` é `Any` e o `payload` do
    registro é um `Mapping` (pode ser um `dict` mutável dentro de uma dataclass congelada). Sem
    isto, alguém poderia trocar a aprovação por outra de mesmo `id` e atributos diferentes (ou
    mutar o payload) entre `authorize_step` e `run_step`.
    """
    if approval is None:
        return None
    if not dataclasses.is_dataclass(approval) or isinstance(approval, type):
        return ["opaco", type(approval).__qualname__, repr(approval)]
    campos: dict[str, Any] = {}
    for f in dataclasses.fields(approval):
        valor = getattr(approval, f.name)
        if f.name == "payload":
            try:
                valor = content_hash(valor)  # o conteúdo de agora, não uma referência
            except (TypeError, ValueError):
                valor = "nao_serializavel"
        elif hasattr(valor, "isoformat"):
            valor = valor.isoformat()
        elif hasattr(valor, "value"):  # enum (Status)
            valor = valor.value
        campos[f.name] = valor
    return campos


def _seal_fields(step: AuthorizationStep, request: ActionRequest, ctx: Context) -> list[Any]:
    d, e = step.decision, step.entry
    return [
        request.action,
        request.target,
        step.payload_hash,
        hashlib.sha256(step.payload_canonical).hexdigest(),
        step.approval_id,
        _approval_fingerprint(ctx.approval),
        step.correlation_id,
        step.timeout_ms,
        ctx.now.isoformat(),
        [d.action, d.allowed, d.reason, d.level.value if d.level is not None else None],
        [
            e.event,
            e.action,
            e.target,
            e.approval_id,
            e.payload_hash,
            e.decision,
            e.reason,
            e.at.isoformat(),
            e.result,
            e.error,
            e.duration_ms,
            e.correlation_id,
            e.timeout_ms,
        ],
    ]


def _entry(
    step: AuthorizationStep,
    request: ActionRequest,
    ctx: Context,
    event: str,
    outcome: str,
    reason: str,
) -> AuditEntry:
    return AuditEntry(
        event=event,
        action=request.action,
        target=request.target,
        approval_id=step.approval_id,
        payload_hash=step.payload_hash,
        decision=outcome,
        reason=reason,
        at=ctx.now,
        correlation_id=step.correlation_id,
    )


def authorize_step(
    request: ActionRequest,
    ctx: Context,
    registry: Mapping[str, ActionSpec] = REGISTRY,
    *,
    correlation_id: str | None = None,
    timeout_ms: int | None = None,
) -> AuthorizationStep:
    """Passo determinístico: autoriza sem executar, gravar nem gerar identificadores.

    `correlation_id` e `timeout_ms` chegam prontos de quem chama. `timeout_ms=None` é válido aqui
    (compatibilidade com `execute()` legado); quem exige timeout é `execute_and_record`. Nunca
    lança por payload inválido (falha fechada).
    """
    approval_id = ctx.approval.id if ctx.approval is not None else None
    try:
        # ÚNICA leitura do payload do chamador: tudo (hash, política, execução) usa este snapshot.
        canonical = canonical_json(request.payload)
    except (TypeError, ValueError):
        canonical = b""
        payload_hash = "nao_serializavel"
        decision = Decision(request.action, False, "payload_nao_serializavel")
    else:
        payload_hash = hashlib.sha256(canonical).hexdigest()
        snapshot = ActionRequest(
            request.action, request.target, json.loads(canonical), request.trigger
        )
        decision = authorize(snapshot, ctx, registry)  # a política avalia o que será executado

    entry = AuditEntry(
        event="authorization",
        action=request.action,
        target=request.target,
        approval_id=approval_id,
        payload_hash=payload_hash,
        decision="allowed" if decision.allowed else "denied",
        reason=decision.reason,
        at=ctx.now,
        correlation_id=correlation_id,
        # o timeout só é registrado quando a execução foi permitida
        timeout_ms=timeout_ms if decision.allowed else None,
    )
    unsealed = AuthorizationStep(
        decision,
        entry,
        payload_hash,
        approval_id,
        correlation_id,
        entry.timeout_ms,
        payload_canonical=canonical,
    )
    return AuthorizationStep(
        decision,
        entry,
        payload_hash,
        approval_id,
        correlation_id,
        entry.timeout_ms,
        payload_canonical=canonical,
        seal=_sign(_seal_fields(unsealed, request, ctx)),
    )


def _check_step(step: AuthorizationStep, request: ActionRequest, ctx: Context) -> None:
    """Falha fechada: recusa passo fabricado, adulterado ou de outro pedido/contexto."""
    if not isinstance(step, AuthorizationStep) or not _verify(
        _seal_fields(step, request, ctx), step.seal
    ):
        raise InvalidAuthorizationStep("passo de autorização inválido, adulterado ou fabricado")
    if step.decision.allowed:
        if hashlib.sha256(step.payload_canonical).hexdigest() != step.payload_hash:
            raise InvalidAuthorizationStep("snapshot do payload não corresponde ao autorizado")
        if content_hash(request.payload) != step.payload_hash:
            # o chamador mudou o payload depois da autorização: falha fechada
            raise InvalidAuthorizationStep("o payload não é o que foi autorizado")


def run_step(
    step: AuthorizationStep,
    request: ActionRequest,
    ctx: Context,
    executor: Executor,
) -> ExecutionResult:
    """Passo **não puro**: chama o executor, que pode produzir efeitos externos.

    Verifica o selo **antes de qualquer coisa**: um passo fabricado nunca chega ao executor
    (`InvalidAuthorizationStep`). O executor recebe exclusivamente o **snapshot canônico** do
    payload autorizado (cópia independente), cujo hash é o validado e auditado. Executa só se a
    autorização permitiu; um passo negado nunca chama o executor. Não grava, não gera
    identificadores e não impõe o timeout (responsabilidade de `persistence.py`). Uma exceção do
    executor vira `decision="error"` em memória, com o nome do tipo em `reason`; a persistência
    traduz isso (`result=error`, `error=<tipo>`, `reason` preservando o da autorização).
    """
    _check_step(step, request, ctx)
    decision = step.decision
    if not decision.allowed:
        return ExecutionResult(decision, ctx.approval, (step.entry,))

    # O executor recebe uma cópia NOVA do snapshot autorizado, nunca `request.payload`: mutações
    # do dict do chamador (inclusive concorrentes, depois da verificação acima) não o alcançam.
    payload = json.loads(step.payload_canonical)
    try:
        result = executor(payload)
    except Exception as exc:  # o erro é registrado só pelo tipo, sem conteúdo
        error = type(exc).__name__
        return ExecutionResult(
            decision,
            ctx.approval,
            (step.entry, _entry(step, request, ctx, "executed", "error", error)),
            error=error,
        )

    approval = ctx.approval
    if decision.level is Level.APPROVAL:
        approval = mark_used(approval, ctx.now)
    return ExecutionResult(
        decision,
        approval,
        (step.entry, _entry(step, request, ctx, "executed", "ok", "executado")),
        result,
    )


def execute(
    request: ActionRequest,
    ctx: Context,
    executor: Executor,
    registry: Mapping[str, ActionSpec] = REGISTRY,
) -> ExecutionResult:
    """Caminho legado: sem timeout e sem registro. Produção usa `execute_and_record`."""
    return run_step(authorize_step(request, ctx, registry), request, ctx, executor)
