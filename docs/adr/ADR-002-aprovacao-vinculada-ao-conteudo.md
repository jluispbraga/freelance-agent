# ADR-002: Aprovação vinculada ao conteúdo exato da ação

**Status:** Proposto, revisão 3 (decisão sobre aprovação com resultado desconhecido incorporada; **implementação ainda não liberada**)
**Data:** 2026-11-19
**Fontes:** design v2.4 §5.6, §5.6.1, §16 (decisão 17); revisão de 18/11/2026 §1.2 (P8, P11), §3.3, R3, R5; plano P9 (ajuste 3, "Fora deste plano"); `AUTHORIZATION.md` (nível "Minha aprovação").

## Contexto

O `AUTHORIZATION.md` exige "Minha aprovação" para enviar proposta, aceitar contrato e entregar ao cliente: o sistema prepara tudo e pede aprovação explícita no Telegram; **silêncio não é aprovação**. A v1 dizia "executa exatamente o conteúdo aprovado", mas não definia hash do conteúdo, uso único, expiração nem verificação de identidade de quem aprova (P8). O bot também autorizava só pelo `chat_id`, sem conferir `from_user.id` nem chat privado (P11).

## Problema

Como garantir que uma aprovação valha **somente** para o conteúdo que o dono viu, **uma única vez**, dentro de um prazo, e dada **somente** por ele?

## Alternativas consideradas

Documentadas nas fontes:
- **v1 (descartada):** a aprovação "executa exatamente o conteúdo aprovado" sem mecanismo. Rejeitada por P8 e por R3 (aprovação reaproveitada para conteúdo diferente).

As fontes não registram outras alternativas deliberadas (por exemplo, aprovação por tipo de ação ou aprovação com validade longa). Elas não são inventadas aqui.

## Decisão

Modelo `ApprovalRequest` com `id` (UUID), `action`, `target_ref`, `payload` (JSON canônico, chaves ordenadas), `content_hash` (SHA-256 do payload canônico), `status` (`pending` | `approved` | `rejected` | `expired` | `superseded`), `created_at`, `expires_at` (padrão 24 h), `decided_at`, `decided_by_user_id`, `decided_chat_id`, `used_at`.

Oito invariantes, cada um com caso de teste obrigatório (CT-AP-1 a CT-AP-8; subcasos AP-01 a AP-22):
1. O pedido guarda o payload canônico e o hash **do que será executado**.
2. A mensagem no Telegram mostra o **conteúdo integral** (ou anexo, se longo, com SHA-256 igual ao `content_hash`) e o prefixo de 8 caracteres do hash.
3. Só conta a decisão do `from_user.id` do dono, em chat privado.
4. **Uso único:** `used_at` é gravado na mesma transação da execução.
5. **Expira**; silêncio não aprova.
6. Na execução, o gateway **recalcula** o hash do payload efetivo e compara com o aprovado; se divergir, nega.
7. Editar o conteúdo marca o pedido como `superseded` e cria outro.
8. Tudo vai para o `ActionLog` (ADR-006), sem o payload.

**Faseamento (decisão registrada):**
- A **lógica pura** está implementada desde a v2.3 (`app/actions/approval.py`), com testes `tests/actions/test_approval_invariants.py`.
- A **persistência** (modelo Django) e a **atomicidade no PostgreSQL** entram com a primeira ação de nível "Minha aprovação" (`proposal.submit`). **Não fazem parte da Fase 2.**
- Até lá, nenhuma ação desse nível tem executor, e o gateway **nega** (AZ-05, AZ-12, AZ-17).
- **AP-12 (execuções concorrentes: só uma vence) é requisito obrigatório antes de habilitar o módulo de propostas** (ajuste 3 da aprovação do P9). O teste AZ-17 impede habilitar `proposal.submit`, `contract.accept` ou `delivery.send` sem aprovação explícita; o AP-12 permanece pulado até lá.

**Aprovação e execução sem conclusão conhecida (decisão do usuário, 19/11/2026).** Quando a autorização de M1 (ADR-001) é confirmada, a aprovação fica **vinculada àquela tentativa**. Se a tentativa terminar com resultado **`unknown`** (por exemplo, o processo cai), a aprovação é considerada **consumida** e **não é reutilizada**, mesmo que a queda tenha ocorrido antes de chamar o executor. É propositalmente conservador: tentar de novo exige **nova avaliação e nova aprovação humana**. Isto vale para o módulo de propostas; nada é implementado na Fase 2.

## Consequências

**Positivas**
- Uma aprovação não pode ser reaproveitada para conteúdo diferente, nem executada duas vezes, nem usada após expirar.
- A regra é testável ponta a ponta e já está coberta na lógica pura.

**Negativas**
- Enquanto a persistência não existe, a garantia de uso único sob **concorrência** não está provada (por isso AP-12 é bloqueante para propostas).
- Qualquer edição gera novo pedido e nova mensagem de aprovação (mais fricção, intencional).

**Neutras**
- A Fase 2 **não** cria tabela de `ApprovalRequest`, e o `ActionLog` apenas registra eventos de aprovação já produzidos pela lógica pura (`AuditEntry.event` = `created`/`callback`/`expired`/`superseded`).

## Pendências (não decididas nas fontes; não assumidas aqui)

| # | Questão | Observação |
|---|---|---|
| P-002.1 | **Mecanismo de atomicidade** do uso único em PostgreSQL (ex.: `UPDATE … WHERE used_at IS NULL`, `SELECT … FOR UPDATE`). O design define o invariante e o teste (AP-12), não o mecanismo. | Decidir junto com o módulo de propostas, antes de habilitá-lo. Não bloqueia a Fase 2. |
| P-002.2 | **Limite de tamanho** que separa "mensagem" de "anexo" (AP-05: "acima do limite de mensagem"). | Fase 4 / módulo de propostas. |
| P-002.3 | **Rotina de expiração** (AP-16: "roda a rotina de expiração"): onde roda e com que frequência não está definido. | Módulo de propostas. |
| P-002.4 | Formato do `ActionLog` para eventos de aprovação | **Resolvida** no ADR-006: eventos `created`, `callback`, `expired`, `superseded` preservados, `approval_id` sem FK, `result` nulo. O vocabulário de `decision` desses eventos aguarda confirmação (ADR-006, P-006.8). |
| P-002.5 | Aprovação com resultado desconhecido | **Resolvida**: aprovação consumida; nova tentativa exige nova aprovação humana. |
| P-002.6 | **Tensão entre três regras, a decidir antes de habilitar propostas.** (a) A invariante 4 diz que `used_at` é gravado "na mesma transação da execução". (b) AP-13 (teste protegido, hoje passando) diz que uma **falha conhecida** do executor **não** consome a aprovação e permite nova tentativa no prazo. (c) O registro em três momentos (ADR-001) confirma a autorização **antes** da execução e não mantém transação aberta durante ela, e a decisão acima consome a aprovação no caso `unknown`. Falta definir: em que momento o consumo é gravado (em M1, de forma atômica, ou em M3), e se uma falha **conhecida** (`result=error`) mantém o comportamento do AP-13. Consumir em M1 simplificaria a atomicidade (AP-12), mas contradiria o AP-13 e exigiria alterar teste protegido. | Aberta. **Não afeta a Fase 2** (nenhuma ação de nível "Minha aprovação" tem executor). Decidir junto com o AP-12, o mecanismo de P-002.1 e o módulo de propostas. |

## Referências
- Design §5.6 e §5.6.1 (CT-AP-1 a CT-AP-8, AP-01 a AP-22)
- Revisão §3.3; riscos R3 e R5
- `app/actions/approval.py`; `tests/actions/test_approval_invariants.py`
- Relacionados: ADR-001, ADR-006
