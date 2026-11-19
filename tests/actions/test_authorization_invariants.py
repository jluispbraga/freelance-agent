"""Invariantes de autorização (AZ-01 a AZ-13).

Cobrem a política global do AUTHORIZATION.md aplicada pela Central de Ações:
consistência documento <-> código, negação por padrão, níveis e os limites das
duas ações de infraestrutura aprovadas em 18/11/2026.
"""

import dataclasses
from pathlib import Path
from types import MappingProxyType

import pytest

from app.actions.authorization_md import parse_authorization_md
from app.actions.policy import ActionRequest, authorize
from app.actions.registry import REGISTRY, Level
from tests.actions.conftest import OWNER, make_context

# Registro só de teste: simula o escopo futuro em que os conectores existirem (Fases 5/6),
# para exercitar as regras de platform.search e platform.oauth_refresh. O registro real
# mantém essas ações SEM executor (AZ-16).
CONNECTOR_SCOPE_REGISTRY = MappingProxyType(
    {
        **REGISTRY,
        **{
            name: dataclasses.replace(REGISTRY[name], has_executor=True)
            for name in ("platform.search", "platform.oauth_refresh")
        },
    }
)

AUTHORIZATION_MD = Path(__file__).resolve().parents[2] / "AUTHORIZATION.md"


def req(action: str, payload: dict | None = None, trigger: str = "system") -> ActionRequest:
    return ActionRequest(action=action, target="t-1", payload=payload or {}, trigger=trigger)


# --- AZ-01 / AZ-02: política única, igual no documento e no código -----------------------


def test_az01_registro_e_authorization_md_sao_identicos():
    rows = parse_authorization_md(AUTHORIZATION_MD.read_text(encoding="utf-8"))
    mapped = {}
    for spec in REGISTRY.values():
        for row in spec.authorization_rows:
            assert row in rows, f"{spec.name} aponta para linha inexistente: {row!r}"
            assert rows[row] == spec.level, f"{spec.name}: nível difere do AUTHORIZATION.md"
            mapped[row] = spec.level
    faltando = set(rows) - set(mapped)
    assert not faltando, f"linhas do AUTHORIZATION.md sem ação no código: {faltando}"


def test_az01b_acoes_de_infraestrutura_aprovadas_estao_no_documento():
    rows = parse_authorization_md(AUTHORIZATION_MD.read_text(encoding="utf-8"))
    assert rows["owner.notify"] == Level.AUTO
    assert rows["platform.oauth_refresh"] == Level.AUTO


def test_az02_registro_nao_pode_ser_alterado_em_execucao():
    with pytest.raises(TypeError):
        REGISTRY["proposal.submit"] = REGISTRY["owner.notify"]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        REGISTRY["proposal.submit"].level = Level.AUTO  # type: ignore[misc]


# --- AZ-03 / AZ-04 / AZ-05 / AZ-06: níveis e negação por padrão --------------------------


def test_az03_acao_desconhecida_e_negada(ctx):
    decision = authorize(req("proposal.submit_now"), ctx)
    assert not decision.allowed
    assert decision.reason == "acao_desconhecida"


@pytest.mark.parametrize("action", ["payment.receive", "payment.withdraw", "payment.anything"])
def test_az04_nivel_eu_e_sempre_negado(action):
    contexto_maximo = make_context(budget_reserved=True, sandboxed=True)
    decision = authorize(req(action), contexto_maximo)
    assert not decision.allowed
    assert decision.reason == "nivel_eu"


@pytest.mark.parametrize("action", ["proposal.submit", "contract.accept", "delivery.send"])
def test_az05_nivel_aprovacao_sem_approval_request_e_negado(ctx, action):
    decision = authorize(req(action), ctx)
    assert not decision.allowed
    assert decision.reason == "aprovacao_ausente"


def test_az06_nivel_isolado_fora_do_sandbox_e_negado(ctx):
    decision = authorize(req("project.develop"), ctx)
    assert not decision.allowed
    assert decision.reason == "fora_do_sandbox"


def test_az06b_nivel_isolado_sem_executor_neste_modulo_e_negado():
    decision = authorize(req("project.develop"), make_context(sandboxed=True))
    assert not decision.allowed
    assert decision.reason == "sem_executor"


def test_az06c_acoes_futuras_automaticas_sem_executor_sao_negadas(ctx):
    decision = authorize(req("proposal.write"), ctx)
    assert not decision.allowed
    assert decision.reason == "sem_executor"


# --- AZ-07: owner.notify, só para o dono -----------------------------------------------


def test_az07_owner_notify_para_o_dono_e_permitido(ctx):
    decision = authorize(req("owner.notify", {"chat_id": OWNER.chat_id, "text": "oi"}), ctx)
    assert decision.allowed


def test_az07b_owner_notify_para_outro_chat_e_negado(ctx):
    decision = authorize(req("owner.notify", {"chat_id": 999, "text": "oi"}), ctx)
    assert not decision.allowed
    assert decision.reason == "destinatario_nao_e_o_dono"


def test_az07c_owner_notify_sem_destinatario_e_negado(ctx):
    decision = authorize(req("owner.notify", {"text": "oi"}), ctx)
    assert not decision.allowed
    assert decision.reason == "destinatario_nao_e_o_dono"


# --- AZ-08: platform.oauth_refresh, sem ampliar permissões e sem outras ações ----------


def test_az08_refresh_com_mesmos_escopos_e_permitido(ctx):
    payload = {"provider": "upwork", "requested_scopes": ["jobs:read", "profile:read"]}
    assert authorize(req("platform.oauth_refresh", payload), ctx, CONNECTOR_SCOPE_REGISTRY).allowed


def test_az08b_refresh_com_escopos_menores_e_permitido(ctx):
    payload = {"provider": "upwork", "requested_scopes": ["jobs:read"]}
    assert authorize(req("platform.oauth_refresh", payload), ctx, CONNECTOR_SCOPE_REGISTRY).allowed


def test_az08c_refresh_que_amplia_escopos_e_negado(ctx):
    payload = {"provider": "upwork", "requested_scopes": ["jobs:read", "proposals:write"]}
    decision = authorize(req("platform.oauth_refresh", payload), ctx, CONNECTOR_SCOPE_REGISTRY)
    assert not decision.allowed
    assert decision.reason == "amplia_permissoes"


def test_az08d_refresh_com_outra_operacao_embutida_e_negado(ctx):
    payload = {
        "provider": "upwork",
        "requested_scopes": ["jobs:read"],
        "then": "submit_proposal",
    }
    decision = authorize(req("platform.oauth_refresh", payload), ctx, CONNECTOR_SCOPE_REGISTRY)
    assert not decision.allowed
    assert decision.reason == "payload_fora_do_escopo_da_acao"


def test_az08e_refresh_de_provedor_sem_concessao_previa_e_negado(ctx):
    payload = {"provider": "freelancer", "requested_scopes": ["basic"]}
    decision = authorize(req("platform.oauth_refresh", payload), ctx, CONNECTOR_SCOPE_REGISTRY)
    assert not decision.allowed
    assert decision.reason == "amplia_permissoes"


# --- AZ-09 / AZ-10: buscas e LLM ------------------------------------------------------


def test_az09_busca_em_fonte_nao_aprovada_e_negada(ctx):
    decision = authorize(
        req("platform.search", {"source": "pendente"}, "schedule"), ctx, CONNECTOR_SCOPE_REGISTRY
    )
    assert not decision.allowed
    assert decision.reason == "fonte_nao_aprovada"


def test_az09b_busca_em_fonte_desconhecida_e_negada(ctx):
    decision = authorize(
        req("platform.search", {"source": "workana"}, "schedule"), ctx, CONNECTOR_SCOPE_REGISTRY
    )
    assert not decision.allowed
    assert decision.reason == "fonte_nao_aprovada"


def test_az09c_fonte_sob_demanda_nunca_por_agendamento(ctx):
    decision = authorize(
        req("platform.search", {"source": "upwork"}, "schedule"), ctx, CONNECTOR_SCOPE_REGISTRY
    )
    assert not decision.allowed
    assert decision.reason == "fonte_somente_sob_demanda"


def test_az09d_fonte_sob_demanda_com_gatilho_do_dono_e_permitida(ctx):
    assert authorize(
        req("platform.search", {"source": "upwork"}, "owner"), ctx, CONNECTOR_SCOPE_REGISTRY
    ).allowed


def test_az09e_fonte_agendada_aprovada_e_permitida(ctx):
    assert authorize(
        req("platform.search", {"source": "freelancer"}, "schedule"), ctx, CONNECTOR_SCOPE_REGISTRY
    ).allowed


def test_az10_llm_sem_reserva_de_orcamento_e_negado(ctx):
    decision = authorize(req("llm.evaluate"), ctx)
    assert not decision.allowed
    assert decision.reason == "orcamento_nao_reservado"


def test_az10b_llm_com_reserva_e_permitido():
    assert authorize(req("llm.evaluate"), make_context(budget_reserved=True)).allowed


# --- AZ-11: toda decisão é explicável --------------------------------------------------


def test_az11_toda_decisao_tem_acao_e_motivo(ctx):
    for action in [*REGISTRY, "inexistente"]:
        decision = authorize(req(action), ctx)
        assert decision.action == action
        assert decision.reason


# --- AZ-12 / AZ-13: as aprovações de infraestrutura não abrem nenhuma outra porta -------


@pytest.mark.parametrize("action", ["proposal.submit", "contract.accept", "delivery.send"])
def test_az12_aprovacao_valida_nao_basta_sem_executor_neste_modulo(action):
    """Mesmo com um ApprovalRequest aprovado para o conteúdo exato, enviar proposta,
    aceitar contrato e entregar continuam impossíveis neste módulo (não há executor)."""
    from app.actions.approval import apply_callback, new_request

    payload = {"texto": "proposta"}
    rec, _ = new_request(action, "t-1", payload, now=make_context().now)
    rec, _ = apply_callback(
        rec,
        "approve",
        user_id=OWNER.user_id,
        chat_id=OWNER.chat_id,
        chat_type="private",
        owner=OWNER,
        now=make_context().now,
    )
    decision = authorize(req(action, payload), make_context(approval=rec))
    assert not decision.allowed
    assert decision.reason == "sem_executor"


def test_az13_nivel_eu_ignora_ate_aprovacao_registrada():
    from app.actions.approval import apply_callback, new_request

    now = make_context().now
    rec, _ = new_request("payment.receive", "t-1", {}, now=now)
    rec, _ = apply_callback(
        rec,
        "approve",
        user_id=OWNER.user_id,
        chat_id=OWNER.chat_id,
        chat_type="private",
        owner=OWNER,
        now=now,
    )
    decision = authorize(req("payment.receive"), make_context(approval=rec))
    assert not decision.allowed
    assert decision.reason == "nivel_eu"


# --- AZ-14 / AZ-15: payload malformado é negado (falha fechada), nunca exceção -----------


def test_az14_payload_com_tipo_invalido_e_negado(ctx):
    decision = authorize(
        req("platform.search", {"source": ["freelancer"]}, "schedule"),
        ctx,
        CONNECTOR_SCOPE_REGISTRY,
    )
    assert not decision.allowed
    assert decision.reason == "payload_invalido"


def test_az15_payload_nao_serializavel_e_negado_no_gateway_sem_chamar_executor(ctx):
    from app.actions.gateway import execute

    chamadas = []
    request = ActionRequest("owner.notify", "t-1", {"chat_id": OWNER.chat_id, "x": {1, 2}})
    result = execute(request, ctx, chamadas.append)
    assert not result.decision.allowed
    assert result.decision.reason == "payload_nao_serializavel"
    assert chamadas == []
    assert result.audit[0].decision == "denied"


# --- AZ-16 / AZ-17: execução só do que está no escopo implementado --------------------


@pytest.mark.parametrize(
    "action,payload,trigger",
    [
        ("platform.search", {"source": "freelancer"}, "schedule"),
        ("platform.search", {"source": "upwork"}, "owner"),
        (
            "platform.oauth_refresh",
            {"provider": "upwork", "requested_scopes": ["jobs:read"]},
            "system",
        ),
    ],
)
def test_az16_conectores_sem_executor_no_registro_real(ctx, action, payload, trigger):
    """Enquanto os conectores não fazem parte do escopo implementado, as permissões
    continuam registradas, mas nenhuma execução é possível, mesmo com contexto válido."""
    decision = authorize(req(action, payload, trigger), ctx)
    assert not decision.allowed
    assert decision.reason == "sem_executor"


def test_az17_executores_habilitados_sao_exatamente_os_do_escopo_atual():
    """Lista fechada. Habilitar qualquer outra ação exige alterar este teste, o que só pode
    ser feito com aprovação explícita do freelancer. Pré-requisitos registrados:
    - platform.search / platform.oauth_refresh: conectores no escopo (Fases 5/6) e
      retenção reavaliada (D4 não é herdada);
    - proposal.submit / contract.accept / delivery.send: AP-12 implementado e passando
      com ApprovalRequest persistido em PostgreSQL."""
    habilitadas = {name for name, spec in REGISTRY.items() if spec.has_executor}
    assert habilitadas == {"llm.evaluate", "owner.notify"}
