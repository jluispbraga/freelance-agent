"""Registro em três momentos da Central de Ações (ADR-001, decisão 9). PROTÓTIPO sem banco.

Caminho de produção para executar ações externas: `execute_and_record`.

- **M1:** autoriza (`authorize_step`) e grava e confirma a linha de autorização, com o
  `correlation_id` e o timeout efetivo. Sem confirmação, o executor não roda (sem *fallback*).
- **M2:** executa **fora de qualquer transação do ActionLog**, com o mesmo timeout gravado em M1 e
  um prazo rígido (`ExecutorTimeout`).
- **M3:** grava o resultado em uma gravação independente.

A gravação é feita por um `ActionLogSink`; a implementação real (conexão independente, cadeia de
hashes, *advisory lock*) é a da Tarefa 2.4. Aqui a lógica é testável sem banco.
"""

import dataclasses
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.actions.approval import mark_used
from app.actions.gateway import ExecutionResult, authorize_step, run_step
from app.actions.policy import ActionRequest, Context, Decision
from app.actions.registry import REGISTRY, ActionSpec, Level

log = logging.getLogger(__name__)

TIMEOUT_NAO_CONFIGURADO = "timeout_executor_nao_configurado"
TENTATIVA_ANTERIOR_EM_ANDAMENTO = "tentativa_anterior_ainda_em_andamento"


class ExecutorTimeout(Exception):
    """O executor ultrapassou o prazo. O efeito externo pode ou não ter ocorrido."""


class ActionLogUnavailable(Exception):
    """Não foi possível gravar e confirmar a autorização (M1): o executor não é chamado."""


@dataclass(frozen=True)
class LogRow:
    """Linha no modelo **persistido** (ADR-006 §7). `decision`: autorização; `result`: execução."""

    event: str
    action: str
    target: str
    payload_hash: str
    decision: str
    reason: str
    result: str | None
    error: str | None
    duration_ms: int | None
    timeout_ms: int | None
    correlation_id: str | None
    approval_id: str | None
    at: datetime


class ActionLogSink(Protocol):
    def append(self, row: LogRow) -> None:
        """Grava **e confirma** a linha, de forma independente de qualquer transação de negócio."""


class TimedExecutor(Protocol):
    def __call__(self, payload: Mapping[str, Any], *, timeout_ms: int, correlation_id: str) -> Any:
        """Executor que recebe o timeout efetivo e o identificador da tentativa."""


# --- Trabalho abandonado por timeout ----------------------------------------------------
# Quando o prazo estoura, a espera é cancelada, mas o trabalho do executor NÃO pode ser
# interrompido à força nem se assume que o efeito externo foi desfeito. Para que esse trabalho
# tardio não seja confundido com uma nova execução: (1) o resultado tardio é descartado e só vai
# para o log da aplicação, com o identificador da tentativa original; (2) enquanto o trabalho
# abandonado ainda estiver rodando neste processo, uma nova tentativa para a mesma ação, alvo e
# payload é NEGADA; (3) nunca há reexecução automática.
_abandoned_lock = threading.Lock()
_abandoned: dict[str, tuple[tuple[str, str, str], threading.Thread]] = {}


def _tentativa_abandonada_em_andamento(key: tuple[str, str, str]) -> bool:
    with _abandoned_lock:
        for cid in [c for c, (_, t) in _abandoned.items() if not t.is_alive()]:
            del _abandoned[cid]
        return any(k == key for k, _ in _abandoned.values())


def _run_with_deadline(
    executor: TimedExecutor,
    payload: Mapping[str, Any],
    *,
    timeout_ms: int,
    correlation_id: str,
    key: tuple[str, str, str],
) -> Any:
    outcome: dict[str, Any] = {}
    done = threading.Event()
    abandoned = threading.Event()

    def work() -> None:
        try:
            outcome["value"] = executor(
                payload, timeout_ms=timeout_ms, correlation_id=correlation_id
            )
        except BaseException as exc:  # noqa: BLE001 - repassado ao chamador ou descartado
            outcome["error"] = exc
        finally:
            done.set()
            if abandoned.is_set():
                # só identificadores; nunca conteúdo
                log.warning("resultado_tardio_descartado correlation_id=%s", correlation_id)

    thread = threading.Thread(target=work, daemon=True, name=f"executor-{correlation_id}")
    thread.start()
    if not done.wait(timeout_ms / 1000):
        with _abandoned_lock:
            _abandoned[correlation_id] = (key, thread)
        abandoned.set()
        if done.is_set():  # terminou exatamente agora: ainda assim vale o prazo estourado
            log.warning("resultado_tardio_descartado correlation_id=%s", correlation_id)
        raise ExecutorTimeout(f"prazo de {timeout_ms} ms excedido")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _denied(request: ActionRequest, reason: str, level: Level | None) -> Decision:
    return Decision(request.action, False, reason, level)


def execute_and_record(
    request: ActionRequest,
    ctx: Context,
    executor: TimedExecutor,
    *,
    sink: ActionLogSink,
    timeouts_ms: Mapping[str, int],
    registry: Mapping[str, ActionSpec] = REGISTRY,
    new_correlation_id: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> ExecutionResult:
    # Identificador e timeout são obtidos AQUI e passados ao gateway (que não gera nada).
    correlation_id = new_correlation_id()
    timeout_ms = timeouts_ms.get(request.action)
    if timeout_ms is not None and timeout_ms <= 0:
        timeout_ms = None
    step = authorize_step(
        request, ctx, registry, correlation_id=correlation_id, timeout_ms=timeout_ms
    )
    key = (request.action, request.target, step.payload_hash)

    # Recusas de persistência: ainda são autorizações NEGADAS e deixam linha (M1 negado).
    denial = None
    if step.decision.allowed and timeout_ms is None:
        denial = TIMEOUT_NAO_CONFIGURADO
    elif step.decision.allowed and _tentativa_abandonada_em_andamento(key):
        denial = TENTATIVA_ANTERIOR_EM_ANDAMENTO
    if denial is not None:
        decision = _denied(request, denial, step.decision.level)
        _append_m1(sink, _row_denied(step, ctx, request, denial))
        return ExecutionResult(decision, ctx.approval, ())

    # --- M1 ----------------------------------------------------------------------------
    _append_m1(sink, _row_authorization(step, ctx, request))
    if not step.decision.allowed:
        return ExecutionResult(step.decision, ctx.approval, (step.entry,))

    # --- M2 (fora de qualquer transação do ActionLog; mesmo timeout gravado em M1) ----------
    started = time.perf_counter()

    def timed(payload: Mapping[str, Any]) -> Any:
        return _run_with_deadline(
            executor, payload, timeout_ms=timeout_ms, correlation_id=correlation_id, key=key
        )

    result = run_step(step, request, ctx, timed)
    duration_ms = int((time.perf_counter() - started) * 1000)

    # --- M3 ------------------------------------------------------------------------------
    unknown = result.error == ExecutorTimeout.__name__
    if unknown and result.decision.level is Level.APPROVAL and ctx.approval is not None:
        # resultado desconhecido: a aprovação é consumida (ADR-002); falha conhecida (`error`)
        # continua como no AP-13, e o `ok` já foi consumido pelo `run_step`.
        result = dataclasses.replace(result, approval=mark_used(ctx.approval, ctx.now))
    try:
        sink.append(_row_executed(step, ctx, request, result, unknown, duration_ms, timeout_ms))
    except Exception:  # noqa: BLE001 - a conciliação tratará a autorização sem conclusão
        log.error("falha_ao_gravar_resultado correlation_id=%s", correlation_id)
    return result


def _append_m1(sink: ActionLogSink, row: LogRow) -> None:
    try:
        sink.append(row)
    except Exception as exc:
        raise ActionLogUnavailable("autorização não confirmada no ActionLog") from exc


def _row_authorization(step, ctx: Context, request: ActionRequest) -> LogRow:
    allowed = step.decision.allowed
    return LogRow(
        event="authorization",
        action=request.action,
        target=request.target,
        payload_hash=step.payload_hash,
        decision="allowed" if allowed else "denied",
        reason=step.decision.reason,
        result="unknown" if allowed else None,  # execução ainda sem resultado conhecido
        error=None,
        duration_ms=None,
        timeout_ms=step.timeout_ms if allowed else None,
        correlation_id=step.correlation_id,
        approval_id=step.approval_id,
        at=ctx.now,
    )


def _row_denied(step, ctx: Context, request: ActionRequest, reason: str) -> LogRow:
    return dataclasses.replace(
        _row_authorization(step, ctx, request),
        decision="denied",
        reason=reason,
        result=None,
        timeout_ms=None,
    )


def _row_executed(
    step,
    ctx: Context,
    request: ActionRequest,
    result: ExecutionResult,
    unknown: bool,
    duration_ms: int,
    timeout_ms: int,
) -> LogRow:
    if unknown:
        outcome, error = "unknown", ExecutorTimeout.__name__
    elif result.error is not None:
        outcome, error = "error", result.error  # só o nome do tipo da exceção
    else:
        outcome, error = "ok", None
    return LogRow(
        event="executed",
        action=request.action,
        target=request.target,
        payload_hash=step.payload_hash,
        decision="allowed",
        reason=step.decision.reason,  # motivo ORIGINAL da autorização, nunca o nome da exceção
        result=outcome,
        error=error,
        duration_ms=duration_ms,
        timeout_ms=timeout_ms,  # o valor registrado em M1
        correlation_id=step.correlation_id,
        approval_id=step.approval_id,
        at=ctx.now,
    )
