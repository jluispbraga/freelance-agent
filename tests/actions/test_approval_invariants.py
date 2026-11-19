"""Invariantes do ApprovalRequest: casos CT-AP-1 a CT-AP-8 e subcasos AP-01 a AP-22
(design, seção 5.6.1).

Nível desta suíte: lógica pura (sem banco e sem Telegram). Os pontos que dependem do
PostgreSQL real (atomicidade entre transações concorrentes, AP-12) e da persistência do
ActionLog ficam marcados para a Fase 2.
"""

import hashlib
from datetime import timedelta
from types import MappingProxyType

import pytest

from app.actions.approval import (
    Status,
    apply_callback,
    canonical_json,
    content_hash,
    expire_if_due,
    new_request,
    render_approval_message,
    supersede,
)
from app.actions.gateway import execute
from app.actions.policy import ActionRequest
from app.actions.registry import REGISTRY, ActionSpec, Level
from tests.actions.conftest import NOW, OWNER, make_context

# Ação de nível "Minha aprovação" que só existe nos testes (o registro real não muda).
TEST_ACTION = "test.approval_action"
TEST_REGISTRY = MappingProxyType(
    {**REGISTRY, TEST_ACTION: ActionSpec(TEST_ACTION, Level.APPROVAL, (), has_executor=True)}
)
TARGET = "proj-42"
PAYLOAD = {
    "texto": "Olá! Posso entregar a API em Django REST Framework.",
    "preco_usd": "2650.00",
    "prazo_dias": 21,
    "anexos": ["portfolio.pdf"],
}


class FakeExecutor:
    """Executor falso: registra o payload recebido e pode falhar sob demanda."""

    def __init__(self, fail: bool = False):
        self.calls: list = []
        self.fail = fail

    def __call__(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError("falha simulada no executor")
        return "ok"


def approved_record(payload=PAYLOAD, now=NOW):
    rec, _ = new_request(TEST_ACTION, TARGET, payload, now=now)
    rec, _ = approve(rec, now=now)
    return rec


def approve(rec, now=NOW, user_id=OWNER.user_id, chat_id=OWNER.chat_id, chat_type="private"):
    return apply_callback(
        rec, "approve", user_id=user_id, chat_id=chat_id, chat_type=chat_type, owner=OWNER, now=now
    )


def run(rec, payload=PAYLOAD, executor=None, now=NOW):
    executor = executor or FakeExecutor()
    request = ActionRequest(TEST_ACTION, TARGET, payload, trigger="owner")
    result = execute(request, make_context(approval=rec, now=now), executor, TEST_REGISTRY)
    return result, executor


# === CT-AP-1 · Invariante 1: payload canônico + hash do que será executado ==============


def test_ap01_hash_independe_da_ordem_das_chaves():
    reordenado = dict(reversed(list(PAYLOAD.items())))
    assert content_hash(PAYLOAD) == content_hash(reordenado)


@pytest.mark.parametrize(
    "campo,valor",
    [("preco_usd", "2650.01"), ("texto", PAYLOAD["texto"] + "!"), ("anexos", ["outro.pdf"])],
)
def test_ap02_hash_muda_com_qualquer_campo(campo, valor):
    assert content_hash({**PAYLOAD, campo: valor}) != content_hash(PAYLOAD)


def test_ap03_executor_recebe_exatamente_o_payload_aprovado():
    rec = approved_record()
    result, executor = run(rec)
    assert result.decision.allowed
    assert len(executor.calls) == 1
    assert canonical_json(executor.calls[0]) == canonical_json(rec.payload)


def test_ct_ap_1_hash_canonico_do_que_sera_executado():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    assert rec.content_hash == hashlib.sha256(canonical_json(PAYLOAD)).hexdigest()
    assert rec.content_hash == content_hash(dict(reversed(list(PAYLOAD.items()))))


# === CT-AP-2 · Invariante 2: mensagem com conteúdo integral + prefixo do hash ===========


def test_ap04_mensagem_contem_conteudo_integral_e_prefixo_do_hash():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    msg = render_approval_message(rec)
    assert msg.attachment is None
    for chave, valor in PAYLOAD.items():
        assert f"{chave}: {valor}" in msg.text
    assert rec.content_hash[:8] in msg.text


def test_ap05_conteudo_longo_vai_como_anexo_com_mesmo_hash():
    longo = {**PAYLOAD, "texto": "x" * 10_000}
    rec, _ = new_request(TEST_ACTION, TARGET, longo, now=NOW)
    msg = render_approval_message(rec, max_chars=3500)
    assert msg.attachment is not None
    assert hashlib.sha256(msg.attachment).hexdigest() == rec.content_hash
    assert rec.content_hash[:8] in msg.text
    assert len(msg.text) <= 3500


# === CT-AP-3 · Invariante 3: só o dono, em chat privado ===============================


@pytest.mark.parametrize(
    "user_id,chat_id,chat_type",
    [
        (999, OWNER.chat_id, "private"),  # AP-06: outro usuário
        (OWNER.user_id, -100123, "group"),  # AP-07: grupo
        (OWNER.user_id, 555, "private"),  # AP-08: dono em outro chat privado
    ],
)
def test_ap06_07_08_decisao_de_quem_nao_e_o_dono_no_chat_dele_e_ignorada(
    user_id, chat_id, chat_type
):
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    novo, audit = approve(rec, user_id=user_id, chat_id=chat_id, chat_type=chat_type)
    assert novo.status is Status.PENDING
    assert audit.decision == "denied"
    assert audit.reason == "decisor_nao_autorizado"


def test_ap09_pedido_rejeitado_nao_executa():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    rec, _ = apply_callback(
        rec,
        "reject",
        user_id=OWNER.user_id,
        chat_id=OWNER.chat_id,
        chat_type="private",
        owner=OWNER,
        now=NOW,
    )
    assert rec.status is Status.REJECTED
    result, executor = run(rec)
    assert not result.decision.allowed
    assert result.decision.reason == "aprovacao_rejeitada"
    assert executor.calls == []


# === CT-AP-4 · Invariante 4: uso único ================================================


def test_ap10_segunda_execucao_e_negada():
    rec = approved_record()
    primeira, _ = run(rec)
    assert primeira.decision.allowed
    assert primeira.approval.used_at == NOW
    segunda, executor = run(primeira.approval)
    assert not segunda.decision.allowed
    assert segunda.decision.reason == "aprovacao_ja_utilizada"
    assert executor.calls == []


def test_ap11_clique_duplo_em_aprovar_e_idempotente():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    uma, _ = approve(rec)
    duas, audit = approve(uma, now=NOW + timedelta(seconds=1))
    assert duas == uma
    assert audit.reason == "ja_decidido"


@pytest.mark.skip(
    reason="Módulo de propostas: exige o ApprovalRequest persistido em PostgreSQL real"
)
def test_ap12_execucoes_concorrentes_so_uma_vence():
    """Duas transações executam o mesmo pedido ao mesmo tempo: exatamente uma vence,
    `used_at` é gravado uma vez (SELECT ... FOR UPDATE na mesma transação da execução)."""


def test_ap13_falha_no_executor_nao_consome_aprovacao():
    rec = approved_record()
    result, executor = run(rec, executor=FakeExecutor(fail=True))
    assert len(executor.calls) == 1
    assert result.error == "RuntimeError"
    assert result.approval.used_at is None
    assert result.audit[-1].decision == "error"
    nova_tentativa, _ = run(result.approval)
    assert nova_tentativa.decision.allowed


# === CT-AP-5 · Invariante 5: expiração; silêncio não aprova ============================


def test_ap14_pedido_expirado_nao_pode_ser_aprovado():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    depois = rec.expires_at + timedelta(seconds=1)
    novo, audit = approve(rec, now=depois)
    assert novo.status is Status.EXPIRED
    assert audit.reason == "expirado"


def test_ap15_aprovado_mas_expirado_antes_da_execucao_e_negado():
    rec = approved_record()
    result, executor = run(rec, now=rec.expires_at)
    assert not result.decision.allowed
    assert result.decision.reason == "aprovacao_expirada"
    assert executor.calls == []


def test_ap16_sem_resposta_vira_expirado():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    antes, nada = expire_if_due(rec, now=rec.expires_at - timedelta(seconds=1))
    assert antes.status is Status.PENDING and nada is None
    depois, audit = expire_if_due(rec, now=rec.expires_at)
    assert depois.status is Status.EXPIRED
    assert audit.event == "expired"
    result, executor = run(depois)
    assert not result.decision.allowed
    assert executor.calls == []


def test_ct_ap_5_padrao_de_expiracao_e_24h():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    assert rec.expires_at - rec.created_at == timedelta(hours=24)


# === CT-AP-6 · Invariante 6: hash recalculado na execução =============================


def test_ap17_payload_alterado_no_armazenamento_apos_aprovacao_e_negado():
    import dataclasses

    rec = approved_record()
    adulterado = dataclasses.replace(rec, payload={**rec.payload, "preco_usd": "1.00"})
    result, executor = run(adulterado, payload=adulterado.payload)
    assert not result.decision.allowed
    assert result.decision.reason == "conteudo_divergente"
    assert executor.calls == []


def test_ap18_payload_efetivo_diferente_do_aprovado_e_negado():
    rec = approved_record()
    result, executor = run(rec, payload={**PAYLOAD, "prazo_dias": 7})
    assert not result.decision.allowed
    assert result.decision.reason == "conteudo_divergente"
    assert executor.calls == []


def test_ct_ap_6_aprovacao_de_outra_acao_ou_alvo_e_negada():
    rec = approved_record()
    request = ActionRequest(TEST_ACTION, "outro-projeto", PAYLOAD, trigger="owner")
    result = execute(request, make_context(approval=rec), FakeExecutor(), TEST_REGISTRY)
    assert not result.decision.allowed
    assert result.decision.reason == "aprovacao_de_outra_acao"


# === CT-AP-7 · Invariante 7: edição gera superseded + novo pedido =====================


def test_ap19_edicao_marca_superseded_e_cria_novo_pedido():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    editado = {**PAYLOAD, "preco_usd": "2800.00"}
    antigo, novo, audit = supersede(rec, editado, now=NOW)
    assert antigo.status is Status.SUPERSEDED
    assert antigo.superseded_by == novo.id
    assert novo.status is Status.PENDING
    assert novo.content_hash == content_hash(editado) != rec.content_hash
    assert render_approval_message(novo).text != render_approval_message(rec).text
    assert audit.event == "superseded"


def test_ap20_aprovar_pedido_superseded_e_negado():
    rec, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    antigo, novo, _ = supersede(rec, {**PAYLOAD, "prazo_dias": 30}, now=NOW)
    ainda_antigo, audit = approve(antigo)
    assert ainda_antigo.status is Status.SUPERSEDED
    assert audit.reason == "substituido"
    assert novo.status is Status.PENDING
    result, executor = run(antigo)
    assert not result.decision.allowed
    assert executor.calls == []


def test_ct_ap_7_aprovado_nao_pode_ser_editado():
    rec = approved_record()
    with pytest.raises(ValueError):
        supersede(rec, {**PAYLOAD, "prazo_dias": 30}, now=NOW)


# === CT-AP-8 · Invariante 8: tudo no ActionLog, sem o conteúdo =========================


def test_ap21_toda_decisao_gera_registro():
    rec, criado = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    _, ignorado = approve(rec, user_id=999)
    aprovado, aprovou = approve(rec)
    executado, _ = run(aprovado)
    negado, _ = run(executado.approval)
    rejeitavel, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    _, rejeitou = apply_callback(
        rejeitavel,
        "reject",
        user_id=OWNER.user_id,
        chat_id=OWNER.chat_id,
        chat_type="private",
        owner=OWNER,
        now=NOW,
    )
    expiravel, _ = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    _, expirou = expire_if_due(expiravel, now=expiravel.expires_at)

    eventos = [criado, ignorado, aprovou, *executado.audit, *negado.audit, rejeitou, expirou]
    assert [e.event for e in eventos] == [
        "created",
        "callback",
        "callback",
        "authorization",
        "executed",
        "authorization",
        "callback",
        "expired",
    ]
    assert [e.decision for e in eventos] == [
        "recorded",
        "denied",
        "approved",
        "allowed",
        "ok",
        "denied",
        "rejected",
        "expired",
    ]
    for e in eventos:
        assert e.action == TEST_ACTION
        assert e.target == TARGET
        assert e.payload_hash == rec.content_hash
        assert e.approval_id is not None


def test_ap22_actionlog_nunca_contem_o_payload():
    rec, criado = new_request(TEST_ACTION, TARGET, PAYLOAD, now=NOW)
    aprovado, aprovou = approve(rec)
    executado, _ = run(aprovado)
    for entry in [criado, aprovou, *executado.audit]:
        serializado = repr(entry)
        assert PAYLOAD["texto"] not in serializado
        assert PAYLOAD["preco_usd"] not in serializado
