"""Três momentos (M1/M2/M3), timeout e `unknown`. Sem banco: o ActionLog é um sink em memória.

A prova contra PostgreSQL real (confirmação em outra conexão, sobrevivência ao rollback do negócio)
é da Tarefa 2.5; aqui se demonstra a lógica de orquestração.
"""

import logging
import threading
import time
from types import MappingProxyType

import pytest

from app.actions import persistence
from app.actions.approval import apply_callback, new_request
from app.actions.persistence import (
    TENTATIVA_ANTERIOR_EM_ANDAMENTO,
    TIMEOUT_NAO_CONFIGURADO,
    ActionLogUnavailable,
    LogRow,
    execute_and_record,
)
from app.actions.policy import ActionRequest
from app.actions.registry import REGISTRY, ActionSpec, Level
from tests.actions.conftest import NOW, OWNER, make_context

REQ = ActionRequest("llm.evaluate", "opp-1", {"texto": "projeto A"})
TIMEOUTS = {"llm.evaluate": 80, "test.approval_action": 80}
APPROVAL_ACTION = "test.approval_action"
TEST_REGISTRY = MappingProxyType(
    {
        **REGISTRY,
        APPROVAL_ACTION: ActionSpec(APPROVAL_ACTION, Level.APPROVAL, (), has_executor=True),
    }
)


@pytest.fixture(autouse=True)
def _drena_trabalho_abandonado():
    """Isolamento: o registro de trabalho abandonado é do processo; espera os fios acabarem."""
    yield
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with persistence._abandoned_lock:
            vivos = [t for _, t in persistence._abandoned.values() if t.is_alive()]
        if not vivos:
            break
        time.sleep(0.02)
    with persistence._abandoned_lock:
        persistence._abandoned.clear()


class MemorySink:
    def __init__(self, fail_on=None):
        self.rows: list[LogRow] = []
        self.fail_on = fail_on  # evento que deve falhar

    def append(self, row):
        if row.event == self.fail_on:
            raise OSError("banco indisponível")
        self.rows.append(row)


class FakeExecutor:
    def __init__(self, sleep=0.0, error=None, on_call=None):
        self.sleep, self.error, self.on_call = sleep, error, on_call
        self.calls = []
        self.finished = threading.Event()

    def __call__(self, payload, *, timeout_ms, correlation_id):
        self.calls.append({"payload": payload, "timeout_ms": timeout_ms, "cid": correlation_id})
        if self.on_call:
            self.on_call()
        try:
            time.sleep(self.sleep)
            if self.error:
                raise self.error
            return "resposta"
        finally:
            self.finished.set()


def run(executor, sink=None, request=REQ, ctx=None, timeouts=TIMEOUTS, registry=TEST_REGISTRY):
    sink = sink or MemorySink()
    ctx = ctx or make_context(budget_reserved=True)
    result = execute_and_record(
        request,
        ctx,
        executor,
        sink=sink,
        timeouts_ms=timeouts,
        registry=registry,
        new_correlation_id=lambda: "corr-1",
    )
    return result, sink


# --- M1 / M2 / M3 em execução normal ---------------------------------------------------


def test_m1_confirmado_antes_do_executor_com_correlation_e_timeout():
    sink = MemorySink()
    visto = {}
    ex = FakeExecutor(on_call=lambda: visto.update(rows=list(sink.rows)))
    result, _ = run(ex, sink)
    (m1,) = visto["rows"]  # só M1 existia quando o executor foi chamado
    assert (m1.event, m1.decision, m1.result) == ("authorization", "allowed", "unknown")
    assert (m1.correlation_id, m1.timeout_ms, m1.error) == ("corr-1", 80, None)
    assert [r.event for r in sink.rows] == ["authorization", "executed"]
    assert result.result == "resposta"


def test_o_timeout_registrado_e_o_aplicado_ao_executor():
    ex = FakeExecutor()
    _, sink = run(ex)
    assert ex.calls[0]["timeout_ms"] == sink.rows[0].timeout_ms == 80
    assert ex.calls[0]["cid"] == "corr-1"


def test_sucesso_grava_allowed_ok():
    _, sink = run(FakeExecutor())
    m3 = sink.rows[1]
    assert (m3.decision, m3.result, m3.error) == ("allowed", "ok", None)
    assert m3.reason == "permitido"


# --- Falha conhecida: reason preservado, tipo em error ---------------------------------


def test_falha_do_executor_preserva_reason_da_autorizacao():
    _, sink = run(FakeExecutor(error=RuntimeError("segredo do cliente")))
    m3 = sink.rows[1]
    assert (m3.decision, m3.result, m3.error) == ("allowed", "error", "RuntimeError")
    assert m3.reason == "permitido"  # o nome da exceção NÃO vai para reason
    assert "segredo" not in repr(sink.rows)


# --- Falha fechada ---------------------------------------------------------------------


def test_falha_em_m1_nao_chama_executor():
    ex = FakeExecutor()
    with pytest.raises(ActionLogUnavailable):
        run(ex, MemorySink(fail_on="authorization"))
    assert ex.calls == []


def test_acao_sem_timeout_grava_negacao_e_nao_chama_executor():
    ex = FakeExecutor()
    result, sink = run(ex, timeouts={})
    assert not result.decision.allowed and result.decision.reason == TIMEOUT_NAO_CONFIGURADO
    assert ex.calls == []
    (linha,) = sink.rows
    assert (linha.decision, linha.reason, linha.result, linha.timeout_ms) == (
        "denied",
        TIMEOUT_NAO_CONFIGURADO,
        None,
        None,
    )


def test_negada_pela_politica_grava_uma_linha_e_nao_chama_executor():
    ex = FakeExecutor()
    result, sink = run(ex, ctx=make_context(budget_reserved=False))
    assert not result.decision.allowed and ex.calls == []
    (linha,) = sink.rows
    assert (linha.decision, linha.reason, linha.result) == (
        "denied",
        "orcamento_nao_reservado",
        None,
    )


def test_falha_em_m3_nao_derruba_o_chamador():
    result, sink = run(FakeExecutor(), MemorySink(fail_on="executed"))
    assert result.result == "resposta"
    assert [r.event for r in sink.rows] == ["authorization"]  # fica para a conciliação


# --- ExecutorTimeout nos três momentos ---------------------------------------------------


def test_executor_que_ultrapassa_o_timeout_registra_unknown_nao_ok_nem_error():
    ex = FakeExecutor(sleep=0.4)
    result, sink = run(ex)
    m1, m3 = sink.rows
    # M1: autorização permitida, resultado ainda desconhecido, timeout e correlação gravados
    assert (m1.decision, m1.result, m1.timeout_ms, m1.correlation_id) == (
        "allowed",
        "unknown",
        80,
        "corr-1",
    )
    # M3: exatamente a linha acordada
    assert (m3.event, m3.decision, m3.result, m3.error) == (
        "executed",
        "allowed",
        "unknown",
        "ExecutorTimeout",
    )
    assert (m3.reason, m3.timeout_ms, m3.correlation_id) == ("permitido", 80, "corr-1")
    assert m3.result not in {"ok", "error"}
    assert result.result is None and result.error == "ExecutorTimeout"


def test_timeout_nao_reexecuta():
    ex = FakeExecutor(sleep=0.4)
    run(ex)
    time.sleep(0.5)
    assert len(ex.calls) == 1


def test_timeout_nao_assume_que_cancelar_desfaz_efeitos_e_descarta_resultado_tardio(caplog):
    caplog.set_level(logging.WARNING)
    ex = FakeExecutor(sleep=0.3)
    result, sink = run(ex)
    assert ex.finished.wait(2)  # o trabalho continuou depois do prazo
    time.sleep(0.1)
    assert [r.event for r in sink.rows] == ["authorization", "executed"]  # sem 3ª linha
    assert sink.rows[1].result == "unknown"  # não virou ok com o resultado tardio
    assert result.result is None
    assert "resultado_tardio_descartado correlation_id=corr-1" in caplog.text
    assert "resposta" not in caplog.text


def test_nova_tentativa_igual_e_negada_enquanto_o_trabalho_abandonado_roda():
    lento = FakeExecutor(sleep=0.6)
    run(lento)  # estoura em 80 ms; o trabalho segue rodando
    novo = FakeExecutor()
    result, sink = run(novo)
    assert not result.decision.allowed
    assert result.decision.reason == TENTATIVA_ANTERIOR_EM_ANDAMENTO
    assert novo.calls == []
    assert sink.rows[0].reason == TENTATIVA_ANTERIOR_EM_ANDAMENTO
    assert lento.finished.wait(2)
    time.sleep(0.05)
    # depois que o trabalho abandonado termina, uma nova tentativa (nova avaliação) é possível
    result2, _ = run(FakeExecutor())
    assert result2.decision.allowed


# --- Aprovação consumida em unknown ----------------------------------------------------


def _aprovacao():
    rec, _ = new_request(APPROVAL_ACTION, "proj-42", {"texto": "proposta"}, now=NOW)
    rec, _ = apply_callback(
        rec,
        "approve",
        user_id=OWNER.user_id,
        chat_id=OWNER.chat_id,
        chat_type="private",
        owner=OWNER,
        now=NOW,
    )
    return rec


def _pedido_aprovado():
    return ActionRequest(APPROVAL_ACTION, "proj-42", {"texto": "proposta"}, trigger="owner")


def test_aprovacao_e_consumida_quando_o_resultado_e_unknown():
    rec = _aprovacao()
    result, sink = run(
        FakeExecutor(sleep=0.4), request=_pedido_aprovado(), ctx=make_context(approval=rec)
    )
    assert sink.rows[1].result == "unknown"
    assert result.approval.used_at == NOW  # consumida; não pode ser reutilizada


def test_aprovacao_e_consumida_no_sucesso():
    rec = _aprovacao()
    result, _ = run(FakeExecutor(), request=_pedido_aprovado(), ctx=make_context(approval=rec))
    assert result.approval.used_at == NOW


def test_falha_conhecida_continua_como_no_ap13_nao_consome():
    """P-002.6 adiado: nenhum invariante protegido é alterado; `error` segue o AP-13."""
    rec = _aprovacao()
    result, sink = run(
        FakeExecutor(error=RuntimeError("x")),
        request=_pedido_aprovado(),
        ctx=make_context(approval=rec),
    )
    assert sink.rows[1].result == "error"
    assert result.approval.used_at is None


def test_executor_do_execute_and_record_recebe_o_snapshot_mesmo_com_mutacao_do_chamador():
    payload = {"texto": "projeto A", "tags": ["a"]}
    pedido = ActionRequest("llm.evaluate", "opp-snapshot", payload)

    def muta_o_original():
        payload["texto"] = "ADULTERADO"
        payload["tags"].append("injetado")

    ex = FakeExecutor(on_call=muta_o_original)
    run(ex, request=pedido)
    assert payload["texto"] == "ADULTERADO"  # a mutação concorrente aconteceu
    assert ex.calls[0]["payload"] == {"texto": "projeto A", "tags": ["a"]}


def test_execute_and_record_executa_o_passo_uma_unica_vez_e_nao_o_expoe():
    ex = FakeExecutor()
    result, sink = run(ex)
    assert len(ex.calls) == 1
    assert [r.event for r in sink.rows].count("executed") == 1
    assert not hasattr(result, "step")  # o passo (e seu selo) não vaza para o chamador
