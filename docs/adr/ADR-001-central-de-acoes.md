# ADR-001: Central de Ações como único caminho para ações externas

**Status:** Proposto, revisão 3 (decisões do usuário de 2026-11-19 aprovadas e incorporadas; **implementação ainda não liberada**, pois falta aprovar as alterações no plano P9)
**Data:** 2026-11-19
**Fontes:** design v2.4 §1.1, §5.5, §5.5.1, §16 (decisões 16, 20, 32–34); revisão de 18/11/2026 §1.2 (P7, P9, P10), §3.1, §3.2; plano P9 (restrições globais, ajustes 1 e 5); `AUTHORIZATION.md`; decisões do usuário P-001.1 e P-001.3.

## Contexto

O `AUTHORIZATION.md` define o que o Freelance Agent faz sozinho e o que depende do freelancer. Vale para todos os módulos, atuais e futuros. Na v1 do design, conectores, cliente do Claude e Telegram seriam chamados diretamente por *services* e *agents*, e nada impedia, por construção, que um módulo novo executasse uma ação externa fora da política (revisão, P7, severidade bloqueante). Também não havia garantia de que o código e o `AUTHORIZATION.md` dissessem a mesma coisa, nem de que a política não pudesse ser afrouxada por configuração (P9).

## Problema

Como garantir, de forma **verificável por ferramenta** e não por convenção, que:
1. toda ação externa passe por uma única camada que aplica a política do `AUTHORIZATION.md`;
2. nenhuma configuração (env, Admin, banco, prompt) afrouxe um nível;
3. código e documento de política não divirjam;
4. o LLM não consiga disparar ações;
5. toda tentativa deixe rastro durável, **inclusive quando a execução falha ou o processo cai**, sem prometer desfazer efeitos externos.

## Alternativas consideradas

Documentadas nas fontes:
- **v1 (descartada):** cada módulo chama seus clientes externos diretamente. Rejeitada por P7: não há ponto único de controle.

Nenhuma outra alternativa foi deliberada formalmente nos documentos de origem. O design apenas cita, em §15, que a **ferramenta** do teste de arquitetura fica a critério de quem implementa, "desde que verifique a regra de importação no CI". O plano P9 fixa `import-linter` (Tarefa 1.5).

Sobre o registro transacional (decisão do usuário), foram consideradas as duas formas que o plano P9 sugeria ao mesmo tempo, e ambas ficam **descartadas**:
- *Uma única transação para autorizar, executar e registrar:* manteria uma transação aberta durante chamadas externas lentas (Anthropic, Telegram) e faria o rollback apagar a própria linha de erro.
- *Registrar só depois da execução:* uma queda do processo no meio deixaria uma ação externa sem nenhum rastro.

## Decisão

1. **Gateway único.** Toda ação externa passa por `app.actions.gateway.execute(...)`.
2. **Registro em código** (`app/actions/registry.py`): imutável em tempo de execução (`MappingProxyType` + *dataclasses* congeladas). Cada ação tem nome, nível e a(s) linha(s) correspondente(s) do `AUTHORIZATION.md`. Nenhuma variável de ambiente, Admin, banco ou prompt altera um nível. Mudar um nível exige mudar o `AUTHORIZATION.md` **e** o registro no mesmo commit.
3. **Nega por padrão**, na ordem do design §5.5: ação fora do registro → nega; nível "Eu" → nega sempre (inclui o prefixo reservado `payment.`); nível "Minha aprovação" → exige `ApprovalRequest` válido (ADR-002), e nega enquanto o modelo não existir; nível isolado → exige contexto de sandbox, e nega neste módulo; ação sem executor → nega; payload malformado → nega (nunca exceção).
4. **Executores** (`app/actions/executors/*`) são os **únicos** módulos que podem importar `anthropic`, `httpx`/`requests` e `telegram`. O contrato é verificado no CI pelo `import-linter` (Tarefa 1.5).
5. **Teste de consistência** (AZ-01) compara a tabela do `AUTHORIZATION.md` com o registro.
6. **Lista fechada de ações executáveis** neste escopo: `{llm.evaluate, owner.notify}` (AZ-17). `platform.search` e `platform.oauth_refresh` ficam registradas **sem executor** (AZ-16) até os conectores entrarem no escopo. Habilitar qualquer outra exige aprovação explícita do usuário. O AP-12 é pré-requisito do módulo de propostas (ADR-002).
7. **O LLM não escolhe ações.** *Tool use* serve só para saída estruturada; nada na resposta do modelo vira chamada ao gateway.
8. **Mapeamento das ações** (design §5.5): `llm.evaluate` ↔ Analisar/Classificar/Calcular preço; `owner.notify` (destinatário exclusivamente o chat privado do dono) e `platform.oauth_refresh` (escopos iguais ou menores, payload só com `provider` e `requested_scopes`) ↔ "Ações de infraestrutura", aprovadas pelo usuário em 18/11/2026, com a ressalva de que **não** autorizam proposta, contrato, entrega nem pagamento.

### 9. Registro em três momentos transacionais (decisão do usuário, P-001.1)

`execute_and_record(request, ctx, executor)` opera assim:

| Momento | O que acontece | Transação |
|---|---|---|
| **M1. Autorização** | Autoriza (função pura) e grava no `ActionLog` a linha `event=authorization` com `decision=allowed` ou `denied`. **Confirma (commit) antes de qualquer outra coisa.** Se negada, é a única linha e o fluxo termina aqui. | Transação própria, curta, **independente** de qualquer transação de negócio |
| **M2. Execução** | Chama o executor. Nenhuma transação do `ActionLog` fica aberta. As alterações locais de negócio que o chamador faça pertencem à transação **dele**, que é revertida se a execução falhar. | Nenhuma do `ActionLog` |
| **M3. Resultado** | Grava `event=executed` com `result=ok` ou `error` (tipo da exceção, sem conteúdo), `duration_ms` e `error`. | **Nova** transação independente |

Regras:
- **Falha gera registro de erro** mesmo que as alterações locais de negócio sejam revertidas, porque M3 não participa da transação de negócio.
- **Não há promessa de rollback de efeitos externos.** Uma mensagem já enviada ao Telegram ou uma chamada já cobrada pela Anthropic não se desfazem. O registro apenas diz o que aconteceu.
- **Falha fechada (aprovada).** Se M1 não puder ser gravado **e confirmado**, o executor não é chamado. **Não existe *fallback* de execução sem `ActionLog`**, nem em indisponibilidade temporária do banco: a chamada falha e o chamador decide se tenta de novo, com uma nova tentativa (novo `correlation_id`).
- **`correlation_id` (aprovado).** É gerado **a cada tentativa de execução**, **persistido em M1 antes de chamar o executor**, e reutilizado nas linhas de resultado (M3) e de conciliação. Tem **índice** para busca, mas **não é único**, pois uma tentativa gera vários eventos.
- **Queda do processo entre M1 e M3** deixa uma linha `authorization`/`allowed` **sem linha `executed`** de mesmo `correlation_id`. A conciliação a identifica e acrescenta `event=reconciled`, `decision=allowed`, `result=unknown`.
- **`unknown` é terminal para execução automática.** A rotina identifica e registra a situação, **nunca reexecuta**. A conciliação **não descobre** o resultado real: apenas registra que a execução permaneceu sem conclusão conhecida. Tentar de novo exige nova avaliação e, quando o nível for "Minha aprovação", **nova aprovação humana** (ADR-002).
- **Conciliação (aprovada).** Executada pelo `worker`, por rotina periódica. Uma autorização só é considerada sem conclusão depois de **o timeout máximo configurado do executor + 2 minutos de tolerância**, para não marcar como desconhecida uma chamada legítima ainda em andamento. Consequência: **cada executor precisa ter um timeout explicitamente configurado**; sem ele, `execute_and_record` recusa a ação (falha fechada) e a conciliação não tem como classificá-la. Os timeouts ficam em configuração (`settings`), **nunca** no `registry.py` (arquivo protegido).
- Se M3 falhar (banco indisponível), o processo registra o fato no log de aplicação (só IDs e hashes) e devolve o resultado ao chamador; a linha `authorization` sem conclusão será tratada pela conciliação.
- O resultado devolvido pelo executor **não é gravado** no `ActionLog`; só o resumo `ok`/`error`/`unknown` (ver ADR-006).

### 10. Impacto no desenho do gateway

O plano P9 (Tarefa 2.5) descreve `execute_and_record` como "chama `execute()` e grava as `AuditEntry`". Isso é incompatível com M1 confirmado antes de M2. A proposta, **a revisar antes de implementar** (alteração controlada de `gateway.py`):
- dividir `execute()` em dois passos puros e públicos, `authorize_step(request, ctx, registry) -> (Decision, AuditEntry, payload_hash)` e `run_step(decision, request, ctx, executor) -> ExecutionResult` (parte pós-autorização, incluindo `mark_used` do `ApprovalRecord`);
- `execute()` continua existindo e passa a ser a composição dos dois passos, **sem mudar comportamento nem assinatura**, para que os 66 testes existentes de `tests/actions/` sigam intactos;
- `execute_and_record` (em `app/actions/persistence.py`) orquestra: `authorize_step` → grava e confirma M1 → `run_step` → grava M3.

## Consequências

**Positivas**
- A política é aplicada em um único ponto e testada (AZ-01 a AZ-17, 66 testes passando, 1 pulado).
- Violações de arquitetura quebram o CI, em vez de depender de revisão.
- Conectores deixam de fazer rede: montam a requisição e traduzem a resposta; só o executor chama a API.
- Nenhuma ação externa acontece sem uma autorização **já confirmada** no banco, e uma execução interrompida deixa rastro detectável.

**Negativas**
- Toda integração nova exige registro, executor e testes antes de funcionar.
- O gateway concentra risco: `policy`, `approval`, `gateway`, `audit` e `authorization_md` são "arquivos de segurança com alteração controlada".
- Duas gravações por execução e a necessidade de uma conexão independente (ADR-006) aumentam a complexidade.
- Uma execução órfã tem resultado **desconhecido** por natureza; a conciliação só pode sinalizar, não resolver.

**Neutras**
- A política só muda por commit; não há ajuste em operação.

## Impactos em testes e interfaces

| Item | Impacto |
|---|---|
| `tests/actions/*` (protegidos) | **Nenhuma alteração** com a opção recomendada no ADR-006. |
| `app/actions/gateway.py` (controlado) | Divisão em dois passos puros; `execute()` preservado como composição. |
| `app/actions/audit.py` (controlado) | Campos opcionais novos (ver ADR-006). **Apresentado para revisão antes de implementar.** |
| `app/actions/persistence.py` (novo) | `execute_and_record` com M1/M2/M3. |
| Tarefa 2.5, testes do plano | Substituir `test_falha_no_executor_grava_erro_e_reverte_efeitos_locais` por testes dos três momentos (lista abaixo). Altera o plano (arquivo protegido): **requer sua aprovação**. |

**Testes propostos para a Tarefa 2.5** (PostgreSQL real, como `fa_app`):
- `test_autorizacao_confirmada_antes_do_executor`: dentro do executor falso, uma segunda conexão já enxerga a linha `authorization`.
- `test_nenhuma_transacao_do_actionlog_aberta_durante_o_executor`.
- `test_negada_grava_uma_linha_e_nao_chama_executor`.
- `test_falha_no_executor_grava_erro_e_reverte_alteracoes_locais`: o chamador altera uma tabela de negócio numa transação; o executor falha; a alteração some e a linha `executed`/`error` permanece.
- `test_falha_ao_gravar_autorizacao_nao_chama_executor`.
- `test_queda_apos_autorizacao_deixa_linha_sem_conclusao` (simula encerrar antes de M3) e `test_conciliacao_registra_resultado_desconhecido_sem_reexecutar`.
- `test_linhas_da_mesma_chamada_compartilham_correlation_id`.
- `test_correlation_id_persistido_em_m1_antes_do_executor`: visto por uma segunda conexão, dentro do executor falso.
- `test_correlation_id_e_indexado_e_nao_unico`.
- `test_sem_actionlog_nao_ha_fallback_de_execucao`: com a gravação de M1 indisponível (falha simulada, ou banco fora), o executor não é chamado.
- `test_executor_sem_timeout_configurado_e_recusado`.
- `test_conciliacao_respeita_timeout_mais_2_minutos`: antes do limiar não marca; depois, marca.
- `test_conciliacao_nunca_reexecuta_e_unknown_e_terminal`: o executor falso não é chamado de novo e nenhuma rotina automática transforma `unknown` em outra coisa.
- `test_conciliacao_e_idempotente`: rodar duas vezes não gera duas linhas `reconciled`.
- `test_linha_nao_contem_payload_nem_retorno_do_executor`.
- `test_chamada_permitida_grava_autorizacao_e_execucao`.

## Pendências (não decididas; não assumidas aqui)

| # | Questão | Situação |
|---|---|---|
| P-001.1 | Transação do `execute_and_record` | **Resolvida** (decisão 9). |
| P-001.2 | Falha ao gravar o `ActionLog` antes de executar | **Resolvida e aprovada** (falha fechada, sem *fallback*). |
| P-001.3 | Mapeamento `AuditEntry` → colunas | **Resolvida** no ADR-006. |
| P-001.4 | **Segredo do Telegram no `worker`.** O design §5.2 não lista `TELEGRAM_BOT_TOKEN` no `worker`, mas ele envia notificações via `owner.notify`. `docs/security/secrets-matrix.md` corrige isso e segue "sujeita à sua revisão". A alternativa é só o `bot` enviar, com o `worker` enfileirando. | Aberta. Não bloqueia a Fase 2. |
| P-001.5 | **`.github/` (workflow e `CODEOWNERS`) não existe** no repositório; confirmar se a Tarefa 1.5 foi concluída. | Aberta. |
| P-001.6 | Limiar e responsável pela conciliação | **Resolvida**: timeout do executor + 2 min; `worker`, rotina periódica. |
| P-001.7 | Divisão de `gateway.py` e `correlation_id` | **Aprovada** (dois passos puros; `execute()` como composição; `correlation_id` indexado, não único). |
| P-001.8 | **Valores dos timeouts** de `llm.evaluate` e `owner.notify`, e se a conciliação lê o timeout da configuração no momento da rotina ou de um valor gravado em M1. Reduzir o timeout depois de uma tentativa em andamento poderia antecipar a marcação. | Aberta, sem bloquear a Fase 2 (os testes usam executor falso com timeout explícito). Os valores reais são definidos nas Fases 3 e 4, quando os executores existirem. |

## Referências
- Design: `docs/superpowers/specs/2026-11-18-descoberta-avaliacao-design.md` §5.5, §5.5.1
- Revisão: `docs/reviews/2026-11-18-revisao-arquitetura.md` §1.2, §3.1, §3.2
- `AUTHORIZATION.md`; `app/actions/{registry,policy,gateway,audit,authorization_md}.py`
- Relacionados: ADR-002 (aprovação), ADR-006 (`ActionLog`)
