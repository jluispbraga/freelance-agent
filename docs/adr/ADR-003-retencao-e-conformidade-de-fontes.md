# ADR-003: Retenção de conteúdo de terceiros e conformidade por fonte

**Status:** Proposto, revisão 3 (decisões P-003.3, P-003.7 e P-003.8 aprovadas e incorporadas; **implementação ainda não liberada**). A exceção D4 é **temporária** (ver Decisão, item 9).
**Data:** 2026-11-19
**Fontes:** design v2.4 §3, §4, §5.4, §6, §6.1, §12, §13, §16 (decisões 18, 19, 25, 30, 31); revisão de 18/11/2026 §1.1 (P1–P6), §1.6 (P21), R1, R9, R11, §3.5, §6.4 (D3/D4); plano P9 (D3, D4, restrições globais).

## Contexto

As plataformas de origem têm restrições próprias:
- **Freelancer.com:** o User Agreement (seção 33) proíbe acesso automatizado, **inclusive pela API**, sem permissão expressa por escrito. Os API Terms proíbem copiar ou armazenar dados, exceto em cache atualizado ao menos a cada 24 h e criptografado.
- **Upwork:** chave com aprovação manual, uso pessoal/interno; busca só para tarefa específica e dirigida pelo usuário; cache limitado a 24 h; coleta do mínimo de dados; proibido usar o conteúdo para treinar, ajustar, *retrieval-augment*, **avaliar ou fazer benchmark** de modelos de IA. Os termos **não dizem explicitamente** se enviar uma vaga a um LLM de terceiros só para inferência é permitido (ambiguidade).

"Gratuita" não significa "permitida para uso automatizado". O usuário decidiu **começar só com a entrada manual** (18/11/2026), de modo que as Fases 1 a 4 não dependem dessas plataformas.

## Problema

Como (a) só buscar em fontes cuja conformidade esteja comprovada e (b) nunca manter conteúdo de terceiros por mais de 24 h, **contando** atrasos de limpeza, backups, logs e mensagens do Telegram?

## Alternativas consideradas

**Fonte de dados** (design §4): A) entrada manual; **B) APIs oficiais, condicionadas à conformidade por fonte (escolhida, com A sempre ativa)**; C) scraping de HTML (descartada).

**D4, exceção física da retenção com o servidor desligado** (revisão §6.4):

| Opção | Resumo | Situação |
|---|---|---|
| A. Aceitar a exceção documentada | Com o servidor desligado nada apaga; ao religar a limpeza roda primeiro e o evento vira incidente | **Aprovada temporariamente** pelo usuário |
| B. Apagamento criptográfico | Conteúdo cifrado com chave só em memória, trocada a cada poucas horas | Não escolhida; candidata na reavaliação |
| C. Não guardar conteúdo de terceiros | Avaliar na coleta e guardar só números | Não recomendada na revisão |

**D3, onde roda** (revisão §6.4): A) máquina do usuário; B) VPS; C) plataforma gerenciada (não recomendada, pois PITR e backups automáticos conflitam com a retenção e com o `ActionLog`). Decidido: A nas Fases 1 a 4, VPS reavaliado antes da Fase 5.

## Decisão

1. **Conformidade por fonte.** Cada `Source` tem `compliance_status` (`pending` | `approved` | `denied`), `compliance_evidence`, `compliance_checked_at` e `terms_version_checked`. O gateway só permite `platform.search` para fonte `approved` (ADR-001). Não existe endpoint que mude o status sem registro de evidência.
2. **Modos.** O agendador só considera fontes `scheduled` **e** `approved`. A Upwork é `on_demand` por definição no código. A entrada manual está sempre ativa.
3. **Regra única de retenção:** conteúdo de terceiros, e o texto derivado dele, existe por **no máximo 24 h** desde `fetched_at`. A constante `THIRD_PARTY_CONTENT_MAX_HOURS = 24` **não é configurável**; `Source.content_ttl_hours` é validado contra ela. Manual: sem expiração.
4. **Tabelas separadas** para o conteúdo que expira: `OpportunityContent` e `EvaluationContent` (1:1 com as tabelas duráveis). Os dados duráveis (`Opportunity`, `Evaluation`) guardam só IDs, URL, notas, preço, recomendação, estado, feedback e hashes.
5. **Linha do tempo:** `expires_at = fetched_at + content_ttl_hours − 4 h` (margem fixa de 4 h, ou seja 20 h para TTL de 24 h). Depois disso a aplicação não lê mais o conteúdo, mesmo com a limpeza atrasada, porque as consultas usam um *manager* que filtra `expires_at > now()`. Limpeza a cada 15 min, em **dois processos independentes** (`worker` e `bot`), também na inicialização, antes de qualquer outra tarefa; idempotente.
6. **Métrica única `content_max_age_hours`** (maior `now() − fetched_at` entre as linhas existentes de fontes de terceiros): < 22 normal; ≥ 22 alerta; ≥ 23 contenção (suspende coleta e avaliação); ≥ 24 violação do teto.
7. **Cópias:** backups `pg_dump` com `--exclude-table-data` das duas tabelas de conteúdo; **sem** WAL/PITR nem *snapshot* de disco do volume; logs e `ActionLog` nunca contêm conteúdo de terceiros; *fixtures* de conectores **sintéticas**; golden set só com exemplos escritos ou parafraseados pelo usuário, **nunca** conteúdo da Upwork.
8. **Upwork:** `raw_payload` sempre nulo; conteúdo mínimo; conector só liga após esclarecimento por escrito sobre inferência por LLM de terceiros; nunca em golden set, calibração ou *few-shot*.
9. **D4 (temporária).** A alternativa A vale **só enquanto houver apenas entrada manual** (ou seja, nenhum conteúdo de terceiros). **Antes de habilitar qualquer conector, a retenção deve ser reavaliada; a exceção não é herdada pela Fase 5.**

10. **Acesso ao conteúdo: filtrado por padrão, completo só para operações internas** (decisão do usuário, P-003.3).
    - `OpportunityContent` **e** `EvaluationContent` declaram dois managers: `visible` (filtra `expires_at IS NULL OR expires_at > now()`) e `unfiltered` (todas as linhas). `visible` é declarado primeiro e, portanto, é o *default manager*. **Não existe `objects`**, para não haver um atalho sem filtro por acidente.
    - API, Admin e serviços comuns usam só `visible`. O Admin e o DRF herdam o *default manager*, e os testes confirmam isso.
    - `unfiltered` é restrito a **operações internas de limpeza e conformidade**: a rotina de retenção (`purge_expired`), a métrica `content_max_age_hours` (que precisa enxergar as linhas expiradas que ainda existem) e a verificação de conformidade. Um teste de arquitetura varre o código e falha se `unfiltered` aparecer fora de uma lista fechada de módulos.
    - **Armadilha do Django, tratada explicitamente:** o *base manager* (usado em exclusões em cascata e em acessos por relação) **não pode** filtrar; se filtrasse, apagar uma oportunidade deixaria para trás as linhas de conteúdo expiradas. Por isso o filtro vive no *default manager*, não no *base manager*. Como o acesso pela relação reversa (`opportunity.content`) passa pelo *base manager* e **não** é filtrado, a relação reversa é desativada (`related_name="+"`) e o acesso ao conteúdo de uma oportunidade/avaliação é feito por funções explícitas que usam `visible` (por exemplo, `OpportunityContent.visible.filter(opportunity=...)`). Evitar `select_related`/`prefetch_related` sobre essas tabelas.

11. **Relógio do filtro: o do banco** (decisão do usuário, P-003.7). `visible` compara `expires_at` com o relógio do banco, nunca com o relógio do processo Python. **Correção do usuário a uma nota anterior:** no PostgreSQL, o `Now()` do Django usa `STATEMENT_TIMESTAMP()`, e não o instante de início da transação (`CURRENT_TIMESTAMP`/`now()`). Portanto:
    - os testes de fronteira validam a **semântica real da consulta SQL**, e não dependem de congelar o relógio Python (nada de `freezegun` ou `mock` sobre `datetime` para o filtro);
    - fixtures definem `expires_at`/`fetched_at` explicitamente, com deslocamentos calculados **no próprio banco** a partir do mesmo relógio do filtro;
    - quando for preciso comparar no mesmo instante exato, a comparação é feita **dentro do banco**, em uma única instrução SQL;
    - funções de serviço com horário como parâmetro (`purge_expired(now)`, Tarefa 3.6) continuam recebendo-o explicitamente, e isso é independente do filtro.
12. **Admin: só conteúdo ainda visível** (decisão do usuário, P-003.8). O Admin dos modelos de conteúdo (se existir) usa o *default manager* filtrado e não é uma porta alternativa para ler conteúdo expirado. `unfiltered` fica restrito aos serviços internos de limpeza e conformidade.

13. **Regras de modelo para conteúdo futuro da Upwork (decisão do usuário, P-003.5).**
    - Entrada manual continua sob a exceção temporária D4, sem expiração.
    - Conteúdo obtido da Upwork: `raw_payload` **sempre nulo**.
    - Conteúdo da Upwork e o texto derivado dele: TTL máximo de **24 h**.
    - **Camada de imposição:** validação no modelo (`save()`/`clean()` das tabelas de conteúdo e de `Source`) e `CHECK` no banco para o que couber numa única tabela (`Source.content_ttl_hours ≤ 24`). A regra de `raw_payload` depende de outra tabela (`Source`), então fica em validação de modelo, com teste.
    - **Nenhuma dessas regras autoriza implementar o conector da Upwork** (nem qualquer conector). `platform.search` segue sem executor (AZ-16).

## Consequências

**Positivas**
- O teto de 24 h é verificável por métrica e por testes com relógio controlado.
- Nenhum backup contém conteúdo de terceiros.
- A negativa ou a demora das plataformas não bloqueia as Fases 1 a 4.

**Negativas**
- Entradas manuais também ficam fora do backup (troca aceita pela simplicidade, design §6.1).
- Sem PITR nem *snapshots*, a recuperação é menos granular.
- Com a máquina do usuário desligada, a retenção depende da exceção D4; vale só sem conteúdo de terceiros.

**Neutras**
- A retenção está pronta para as Fases 5 e 6, mas na Fase 2 só os modelos e as validações são criados; a rotina de limpeza é da Tarefa 3.6.

## Impactos em testes e interfaces

| Item | Impacto |
|---|---|
| Interface da Tarefa 2.1 (plano, protegido) | Passa de "manager `OpportunityContent.visible`" para `visible` e `unfiltered` em **`OpportunityContent` e `EvaluationContent`**: **requer sua aprovação** para alterar o plano. |
| Testes da Tarefa 2.1 | O `test_manager_visible_esconde_conteudo_expirado` passa a cobrir os dois modelos. Novos: `test_default_manager_de_conteudo_e_filtrado`, `test_nao_existe_manager_objects_nos_modelos_de_conteudo`, `test_base_manager_nao_filtra_e_cascata_apaga_conteudo_expirado`, `test_relacao_reversa_para_conteudo_nao_e_exposta`, `test_unfiltered_so_em_modulos_autorizados`. |
| Testes da Tarefa 2.6 | `test_admin_e_api_nao_exibem_conteudo_expirado` (Admin e DRF usam o acesso filtrado). |
| Tarefa 3.6 | `purge_expired` e a métrica usam `unfiltered`; `test_conteudo_manual_nunca_e_apagado` continua valendo. |

## Pendências (não decididas nas fontes; não assumidas aqui)

| # | Questão | Observação |
|---|---|---|
| P-003.1 | **Reavaliação de D4** antes de qualquer conector (opção A, B ou outra, conforme D3 vigente). | Obrigatória antes da Fase 5; não bloqueia a Fase 2. |
| P-003.2 | **Esclarecimento da Upwork** sobre inferência por LLM de terceiros (F7) e **permissão por escrito** do Freelancer.com (F6). | Bloqueiam só as Fases 5 e 6. |
| P-003.3 | Manager de visibilidade para `EvaluationContent` | **Resolvida** (decisão 10): os dois modelos têm `visible` e `unfiltered`. |
| P-003.7 | Relógio do filtro | **Resolvida e aprovada** (decisão 11): relógio do banco. |
| P-003.8 | Admin dos modelos de conteúdo | **Resolvida e aprovada** (decisão 12): só conteúdo visível. |
| P-003.4 | **Onde a margem de 4 h é aplicada** (modelo, serviço ou limpeza). A Tarefa 3.6 testa `test_expires_at_aplica_margem_de_4h`; a Tarefa 2.1 não menciona a margem. | Definir na Tarefa 2.1 apenas o que for necessário para o modelo; o cálculo fica para 3.6. |
| P-003.5 | Imposição de `raw_payload` nulo (Upwork) e TTL ≤ 24 h | **Resolvida** (decisão 13): validação de modelo + `CHECK` no que couber numa tabela. |
| P-003.6 | Hospedagem: **reavaliar VPS antes da Fase 5** (D3). | Fora da Fase 2. |

## Referências
- Design §3, §4, §6, §6.1, §12, §13
- Revisão §1.1, §3.5, §6.4
- Termos: Freelancer.com User Agreement §33 e API Terms; Upwork API & MCP Terms 4.1, 4.2, 5.3, 8.3 (links no design)
- Relacionados: ADR-001, ADR-006
