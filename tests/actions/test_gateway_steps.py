"""Passos do gateway: um `AuthorizationStep` só vale se produzido por `authorize_step`."""

import dataclasses
from datetime import timedelta
from types import MappingProxyType

import pytest

from app.actions.approval import Status, apply_callback, new_request
from app.actions.gateway import (
    AuthorizationStep,
    InvalidAuthorizationStep,
    authorize_step,
    execute,
    run_step,
)
from app.actions.policy import ActionRequest, Decision
from app.actions.registry import REGISTRY, ActionSpec, Level
from tests.actions.conftest import NOW, OWNER, make_context

REQ = ActionRequest("llm.evaluate", "opp-1", {"texto": "projeto A"})


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        return "ok"


def _entry_permitida(request=REQ, payload_hash="x"):
    step = authorize_step(request, make_context(budget_reserved=True))
    return step.entry


def test_run_step_nao_executa_authorization_step_fabricado():
    """Frozen não basta: uma instância montada à mão, com decisão permitida, não executa."""
    ctx = make_context(budget_reserved=True)
    legitimo = authorize_step(REQ, ctx)
    fabricado = AuthorizationStep(
        decision=Decision("llm.evaluate", True, "permitido", Level.AUTO),
        entry=legitimo.entry,
        payload_hash=legitimo.payload_hash,
        approval_id=None,
    )
    spy = Spy()
    with pytest.raises(InvalidAuthorizationStep):
        run_step(fabricado, REQ, ctx, spy)
    assert spy.calls == []


def test_selo_forjado_ou_copiado_nao_executa():
    ctx = make_context(budget_reserved=True)
    legitimo = authorize_step(REQ, ctx)
    spy = Spy()
    ultimo_diferente = "1" if legitimo.seal[-1] == "0" else "0"  # sempre muda o selo
    for seal in ("", "0" * 64, legitimo.seal[:-1] + ultimo_diferente, None):
        with pytest.raises(InvalidAuthorizationStep):
            run_step(dataclasses.replace(legitimo, seal=seal), REQ, ctx, spy)
    assert spy.calls == []


def test_passo_negado_adulterado_para_permitido_nao_executa():
    ctx = make_context(budget_reserved=False)  # orçamento não reservado: negado
    negado = authorize_step(REQ, ctx)
    assert not negado.decision.allowed
    adulterado = dataclasses.replace(
        negado, decision=Decision("llm.evaluate", True, "permitido", Level.AUTO)
    )
    spy = Spy()
    with pytest.raises(InvalidAuthorizationStep):
        run_step(adulterado, REQ, ctx, spy)
    # mutação por fora do `frozen` também é detectada
    object.__setattr__(negado, "decision", Decision("llm.evaluate", True, "permitido", Level.AUTO))
    with pytest.raises(InvalidAuthorizationStep):
        run_step(negado, REQ, ctx, spy)
    assert spy.calls == []


def test_passo_legitimo_nao_autoriza_outro_payload_nem_outro_alvo_nem_outro_contexto():
    ctx = make_context(budget_reserved=True)
    passo = authorize_step(REQ, ctx)
    spy = Spy()
    outro_payload = ActionRequest("llm.evaluate", "opp-1", {"texto": "projeto B"})
    outro_alvo = ActionRequest("llm.evaluate", "opp-2", {"texto": "projeto A"})
    outro_ctx = make_context(budget_reserved=True, now=NOW.replace(minute=1))
    for pedido, contexto in ((outro_payload, ctx), (outro_alvo, ctx), (REQ, outro_ctx)):
        with pytest.raises(InvalidAuthorizationStep):
            run_step(passo, pedido, contexto, spy)
    assert spy.calls == []


def test_passo_legitimo_executa_uma_vez_pelo_caminho_normal():
    ctx = make_context(budget_reserved=True)
    spy = Spy()
    result = run_step(authorize_step(REQ, ctx), REQ, ctx, spy)
    assert result.decision.allowed and len(spy.calls) == 1


def test_authorize_step_e_deterministico_e_nao_gera_identificadores():
    ctx = make_context(budget_reserved=True)
    a = authorize_step(REQ, ctx, correlation_id="c1", timeout_ms=5000)
    b = authorize_step(REQ, ctx, correlation_id="c1", timeout_ms=5000)
    assert a == b and a.seal == b.seal
    assert authorize_step(REQ, ctx).correlation_id is None  # nada é gerado por dentro


def test_timeout_so_e_registrado_quando_permitido():
    permitido = authorize_step(REQ, make_context(budget_reserved=True), timeout_ms=5000)
    negado = authorize_step(REQ, make_context(budget_reserved=False), timeout_ms=5000)
    assert permitido.timeout_ms == 5000 and permitido.entry.timeout_ms == 5000
    assert negado.timeout_ms is None and negado.entry.timeout_ms is None


def test_execute_legado_mantem_comportamento():
    ctx = make_context(budget_reserved=True)
    spy = Spy()
    result = execute(REQ, ctx, spy)
    assert result.decision.allowed
    assert [e.event for e in result.audit] == ["authorization", "executed"]
    assert [e.decision for e in result.audit] == ["allowed", "ok"]


# --- vínculo integral da aprovação ao selo (não só o approval_id) ---------------------------

APROVACAO_ACAO = "test.approval_action"
APROVACAO_REGISTRY = MappingProxyType(
    {**REGISTRY, APROVACAO_ACAO: ActionSpec(APROVACAO_ACAO, Level.APPROVAL, (), has_executor=True)}
)
PEDIDO_APROVADO = ActionRequest(APROVACAO_ACAO, "proj-42", {"texto": "proposta"}, trigger="owner")


def _aprovacao():
    rec, _ = new_request(APROVACAO_ACAO, "proj-42", {"texto": "proposta"}, now=NOW)
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


def test_o_contexto_e_o_registro_de_aprovacao_sao_imutaveis_por_contrato_exceto_o_payload():
    """Documenta o contrato: Context e ApprovalRecord são frozen, mas `payload` é um Mapping
    (pode ser um dict mutável) e `Context.approval` é `Any`. Por isso o selo não confia nisso."""
    rec = _aprovacao()
    assert dataclasses.is_dataclass(rec) and rec.__dataclass_params__.frozen
    assert make_context().__dataclass_params__.frozen
    assert isinstance(rec.payload, dict)  # mutável: o selo precisa cobri-lo


@pytest.mark.parametrize(
    "alteracao",
    [
        {"status": Status.REJECTED},
        {"expires_at": NOW + timedelta(days=30)},
        {"used_at": NOW},
        {"decided_by_user_id": 999},
        {"decided_chat_id": 999},
        {"superseded_by": "outro"},
        {"content_hash": "0" * 64},
        {"created_at": NOW - timedelta(days=1)},
        {"payload": {"texto": "OUTRO TEXTO"}},
    ],
)
def test_aprovacao_substituida_por_outra_com_o_mesmo_id_e_atributos_diferentes_e_rejeitada(
    alteracao,
):
    rec = _aprovacao()
    ctx = make_context(approval=rec)
    passo = authorize_step(PEDIDO_APROVADO, ctx, APROVACAO_REGISTRY)
    assert passo.decision.allowed
    impostor = dataclasses.replace(rec, **alteracao)  # mesmo id, atributos diferentes
    assert impostor.id == rec.id
    spy = Spy()
    with pytest.raises(InvalidAuthorizationStep):
        run_step(passo, PEDIDO_APROVADO, make_context(approval=impostor), spy)
    assert spy.calls == []


def test_payload_da_aprovacao_mutado_no_lugar_entre_autorizar_e_executar_e_rejeitado():
    rec = _aprovacao()
    ctx = make_context(approval=rec)
    passo = authorize_step(PEDIDO_APROVADO, ctx, APROVACAO_REGISTRY)
    rec.payload["texto"] = "mutado depois da autorização"  # dict dentro de um frozen
    spy = Spy()
    with pytest.raises(InvalidAuthorizationStep):
        run_step(passo, PEDIDO_APROVADO, ctx, spy)
    assert spy.calls == []


def test_aprovacao_removida_ou_inserida_apos_a_autorizacao_e_rejeitada():
    rec = _aprovacao()
    com = authorize_step(PEDIDO_APROVADO, make_context(approval=rec), APROVACAO_REGISTRY)
    spy = Spy()
    with pytest.raises(InvalidAuthorizationStep):
        run_step(com, PEDIDO_APROVADO, make_context(approval=None), spy)
    ctx = make_context(budget_reserved=True)
    sem = authorize_step(REQ, ctx)
    with pytest.raises(InvalidAuthorizationStep):
        run_step(sem, REQ, make_context(budget_reserved=True, approval=rec), spy)
    assert spy.calls == []


def test_a_mesma_aprovacao_integra_executa_e_e_consumida():
    rec = _aprovacao()
    ctx = make_context(approval=rec)
    passo = authorize_step(PEDIDO_APROVADO, ctx, APROVACAO_REGISTRY)
    spy = Spy()
    result = run_step(passo, PEDIDO_APROVADO, ctx, spy)
    assert len(spy.calls) == 1 and result.approval.used_at == NOW
