# Revisão de Arquitetura — Freelance Agent (módulo de descoberta e avaliação)

**Data:** 2026-11-18 · **Documento revisado:** `docs/superpowers/specs/2026-11-18-descoberta-avaliacao-design.md` (v1)
**Resultado:** design atualizado para a **v2** no mesmo arquivo. Nada foi implementado.

> **Atualização v2.1 (18/11/2026):** a v2 foi aprovada conceitualmente. Uma revisão pontual ajustou retenção (teto efetivo de 24 h), tributação (estimativa configurável), testes de aprovação (AP-01 a AP-22), imutabilidade do `ActionLog` e o início só com entrada manual. **Nos pontos em que esta revisão diverge do design v2.1, vale o design.** Os trechos abaixo já foram alinhados.

> **Atualização v2.2 (18/11/2026), fechamento:** métrica única de retenção `content_max_age_hours`; oito casos de teste CT-AP-1 a CT-AP-8 (um por invariante); `ActionLog` explicitado como *append-only*; histórico de versões. O **checklist da Fase 0** (seção 6) substitui a tabela da seção 4 como referência operacional. A seção 4 fica como registro histórico.

Escopo da revisão, conforme pedido:
1. `AUTHORIZATION.md` como regra global e inviolável.
2. Toda ação externa passando por uma camada central de autorização.
3. Aprovação explícita e vinculada ao conteúdo exato da ação.
4. Desenvolvimento futuro em containers isolados, sem credenciais nem recursos do host.
5. Premissas sobre as APIs do Freelancer.com e da Upwork.
6. Preço considerando taxas e custos operacionais.
7. Identificadores e preços dos modelos Anthropic.

Legenda de severidade: **Bloqueante** (impede seguir), **Alta** (muda o design), **Média** (ajuste necessário), **Baixa** (melhoria).

---

## 1. Problemas encontrados

### 1.1 Fontes de dados (premissas não garantidas)

| # | Problema | Severidade | Evidência |
|---|---|---|---|
| P1 | **O Freelancer.com exige permissão expressa por escrito para acesso automatizado, inclusive via API.** O design v1 assumia que o polling agendado era permitido só porque a API existe e é gratuita. | **Bloqueante** para a coleta agendada | User Agreement do Freelancer.com, seção 33 |
| P2 | **Os termos da API do Freelancer.com proíbem copiar ou armazenar dados**, exceto em cache por desempenho, atualizado ao menos a cada 24 h e guardado com criptografia forte. O v1 guardava `raw_payload`, descrição e dados do cliente por tempo indeterminado e montava o golden set com projetos reais. | Alta | Freelancer API Terms & Conditions (última modificação: 21/12/2017) |
| P3 | **Os termos da Upwork limitam o cache a 24 h** (seção 8.3) e exigem coletar o mínimo de dados (seção 4.2). O v1 só deixava de guardar o payload bruto, mas mantinha título, descrição e dados do cliente. | Alta | Upwork API & MCP Terms, 4.2 e 8.3 |
| P4 | **A Upwork proíbe usar seu conteúdo para treinar, ajustar (*fine-tune*), fazer *retrieval-augmentation*, avaliar ou fazer *benchmark* de modelos de IA**, inclusive via API de terceiros. O golden set e a calibração por feedback **não podem** usar vagas da Upwork. Os termos **não falam explicitamente** de enviar uma vaga a um LLM de terceiros só para inferência, que é exatamente o uso do avaliador. Há ambiguidade. | **Bloqueante** para o conector Upwork | Upwork API & MCP Terms, 5.3 |
| P5 | Não foi confirmado que uma chave de API de **freelancer** da Upwork recebe o escopo de busca de vagas. A aprovação é manual, e a chave é só para uso pessoal ou interno. | Média | Upwork Help: *Request an API key* |
| P6 | A autenticação do Freelancer.com usa o cabeçalho `freelancer-oauth-v1` e não `Authorization: Bearer` (fonte secundária). O app pode passar por aprovação antes de receber credenciais. Sem permissão (P1), não dá para nem testar a API com scripts. | Média | Guia de integração de terceiros; confirmar no portal oficial |

### 1.2 Autorização

| # | Problema | Severidade |
|---|---|---|
| P7 | **Não existe camada central de autorização.** Conectores, cliente do Claude e Telegram seriam chamados diretamente pelos *services* e *agents*. Nada impede, por construção, que um módulo futuro execute uma ação externa sem passar pela política. | **Bloqueante** (requisito 2) |
| P8 | **A vinculação da aprovação ao conteúdo não tem mecanismo.** O v1 dizia "executa exatamente o conteúdo aprovado", mas não definia hash do conteúdo, uso único, expiração nem verificação de identidade de quem aprova. | Alta (requisito 3) |
| P9 | **Não há garantia de que código e `AUTHORIZATION.md` digam a mesma coisa.** A política também poderia ser afrouxada por configuração (env, Admin). | Alta (requisito 1) |
| P10 | **Ações de infraestrutura não estão mapeadas na política.** Buscar numa plataforma, chamar o LLM, mandar mensagem no Telegram do próprio usuário e renovar token OAuth não aparecem no `AUTHORIZATION.md`. Sem mapeamento, a regra "tudo passa pela política" fica ambígua. | Média |
| P11 | O bot autorizava só pelo `chat_id`. Para aprovar ações, também é preciso conferir o `from_user.id` e aceitar somente chat privado. | Média |

### 1.3 Isolamento do desenvolvimento futuro

| # | Problema | Severidade |
|---|---|---|
| P12 | O v1 não tinha requisitos para o ambiente isolado. A escolha mais comum e perigosa seria montar o `docker.sock` no worker para criar containers, o que equivale a dar root no host. Precisa ser proibido desde já, porque afeta a topologia dos containers de hoje. | Alta (requisito 4) |
| P13 | Os segredos não tinham escopo por container. Todos receberiam o mesmo `.env`. | Média |

### 1.4 Preço

| # | Problema | Severidade | Evidência |
|---|---|---|---|
| P14 | **O preço ignorava a taxa da plataforma.** Freelancer.com: 10% ou US$ 5 (o maior) em projeto fixo e 10% em projeto por hora. Upwork: taxa variável de 0% a 15%, mostrada antes de enviar a proposta, além de Connects a US$ 0,15 cada. | Alta | Página *Fees and Charges* do Freelancer.com; Upwork *Is Upwork free?* e artigos sobre a taxa variável |
| P15 | Também ignorava saque e transferência, *spread* de câmbio, impostos e horas não faturáveis (comunicação, revisões, proposta). | Alta | — |
| P16 | "Taxa-alvo" era ambígua entre valor bruto e líquido. O exemplo do v1 (US$ 2.600) parecia ser o que o usuário receberia, mas era bruto e saía abaixo da meta líquida. | Média | — |

### 1.5 Modelos Anthropic

| # | Problema | Severidade | Evidência |
|---|---|---|---|
| P17 | **Os IDs `claude-haiku-5-5` e `claude-sonnet-5-5` estão corretos**, mas o v1 não tinha tabela de preços e mandava "conferir depois". | Baixa | Página *Models overview* da Anthropic |
| P18 | **O v1 não considerava o raciocínio adaptativo.** O esforço padrão é *high* no Sonnet 5.5 e *medium* no Haiku 5.5. Os tokens de raciocínio contam para o `max_tokens` e são cobrados como saída, o que pode multiplicar o custo da avaliação se o esforço não for definido explicitamente. | Média | Páginas dos modelos e *What's new in Haiku 5.5* |
| P19 | O tokenizador novo gera cerca de 30% mais tokens que os modelos antigos para o mesmo texto. As estimativas de custo precisam ser medidas, não chutadas. | Baixa | *What's new in Haiku 5.5* |
| P20 | **O teto diário era checado só depois da chamada.** Com tarefas concorrentes, o gasto pode passar do teto. | Média | — |

### 1.6 Outros

| # | Problema | Severidade |
|---|---|---|
| P21 | Não havia rotina de expiração e exclusão de dados de terceiros. | Alta (consequência de P2/P3) |
| P22 | O Django Admin e a API ficavam expostos sem definição de rede (porta pública?). | Média |
| P23 | O Procrastinate está ativo (versão 3.5.3), mas o projeto pede mais mantenedores. É risco de continuidade. | Baixa |

---

## 2. Riscos técnicos e de segurança

| # | Risco | Prob. | Impacto | Mitigação no v2 |
|---|---|---|---|---|
| R1 | Banimento da conta numa plataforma por uso fora dos termos | Média | **Alto** (perda de renda) | Fontes só ativam com evidência de conformidade (`compliance_status`); retenção ≤ 24 h; Upwork apenas sob demanda |
| R2 | Ação externa executada fora da política (bug, atalho, módulo novo) | Média | **Alto** | Gateway central com negação por padrão; teste de arquitetura que proíbe importar clientes externos fora dos executores; política só em código, sem *override* |
| R3 | Aprovação reaproveitada para um conteúdo diferente do aprovado | Baixa | **Alto** | Hash do conteúdo canônico, uso único, expiração e reverificação do hash na execução |
| R4 | *Prompt injection* na descrição do projeto altera nota ou, no futuro, o texto da proposta | Alta | Médio | O LLM não tem ferramentas que executam ações; saída restrita ao schema; texto delimitado como dado; toda proposta passa por aprovação com o conteúdo exato |
| R5 | Vazamento do token do bot: um atacante leria as atualizações e veria o conteúdo dos pedidos de aprovação | Baixa | Médio | Bot só em chat privado; checagem de `chat_id` **e** `from_user.id`; procedimento de rotação do token; o callback de aprovação só vale vindo do `user_id` do dono |
| R6 | Fuga do sandbox no desenvolvimento futuro (acesso ao host ou a credenciais) | Média | **Alto** | Requisitos de isolamento (seção 3.4); proibição de `docker.sock` em qualquer container; runner separado e sem segredos |
| R7 | Custo de LLM fora de controle (loop, pico de volume, raciocínio longo) | Média | Médio | Reserva de orçamento antes de cada chamada; concorrência 1 na fila de LLM; `effort` e `max_tokens` explícitos; limite de tamanho da entrada |
| R8 | Preço sugerido abaixo do custo real (taxas não consideradas) | Alta (no v1) | Médio | Fórmula v2 com taxas, saque, câmbio, imposto e horas não faturáveis; tabela de taxas com data de verificação |
| R9 | API mudar ou ser descontinuada (os termos do Freelancer.com permitem isso a qualquer momento) | Média | Médio | Conectores isolados, testados com *fixtures*; a entrada manual continua funcionando sempre |
| R10 | Admin ou API expostos na internet | Média | Alto | `web` ligado só em `127.0.0.1`; acesso por túnel SSH/VPN; `DEBUG=False`; token DRF rotacionável |
| R11 | Dados de terceiros guardados além do permitido (incluindo backups) | Média | Médio | Conteúdo em tabelas próprias, invisível em 20 h, limpeza a cada 15 min em dois processos, alerta em 22 h, contenção em 23 h, teto de 24 h; backups excluem as tabelas de conteúdo (design v2.1, seção 6.1) |
| R12 | Divergência entre `AUTHORIZATION.md` e o código | Média | Alto | Teste que lê a tabela do `.md` e compara com o registro de ações do código |
| R13 | Continuidade do Procrastinate | Baixa | Baixo | Uso só dentro de `app/tasks.py`; trocar por Celery afeta um módulo |

---

## 3. Alterações recomendadas

Princípio: resolver cada requisito com o **mínimo de peças novas**. Ficam mantidas as decisões que já estavam certas: Upwork só sob demanda, entrada manual, Procrastinate, bot em long polling, avaliação em dois estágios, nota final calculada pelo sistema, `PROFILE.md` como fonte única, estrutura de pastas do usuário, sistema de um único usuário.

### 3.1 Central de Ações (requisitos 1 e 2)
Um módulo, `app/actions/`, com três peças:
- **Registro de ações** (`registry.py`): toda ação externa tem um nome, um nível de política e a linha correspondente do `AUTHORIZATION.md`. O registro é **código**, não configuração: nenhum env, Admin ou banco consegue mudar um nível.
- **Gateway** (`gateway.py`): o único caminho para executar uma ação externa. Ele nega por padrão: ação desconhecida é negada, nível "Eu" é sempre negado, nível "Minha aprovação" exige aprovação válida e nível "isolado" exige contexto de sandbox. Toda tentativa, permitida ou negada, gera um registro em `ActionLog`, de apenas inserção. A imutabilidade é garantida no banco: papel de execução sem `UPDATE`/`DELETE`/`TRUNCATE`, *triggers* e cadeia de hashes (design v2.1, seção 5.8).
- **Executores** (`executors/`): os únicos módulos que podem importar `anthropic`, clientes HTTP e `telegram`. Um **teste de arquitetura** (ex.: `import-linter`) quebra o CI se qualquer outro módulo importar essas bibliotecas.

Mais um **teste de consistência**: lê a tabela do `AUTHORIZATION.md` e verifica que cada linha existe no registro com o mesmo nível.

### 3.2 Mapeamento das ações desta fase

| Ação no código | Linha do AUTHORIZATION.md | Nível | Executor nesta fase |
|---|---|---|---|
| `platform.search` (Freelancer.com / Upwork, leitura) | Analisar oportunidades | Automática | Sim (somente fontes com conformidade aprovada) |
| `llm.evaluate` (Anthropic) | Analisar / Classificar / Calcular preço | Automática | Sim |
| `owner.notify` (Telegram para o próprio usuário) | *Infraestrutura (proposta)* | Automática | Sim |
| `platform.oauth_refresh` | *Infraestrutura (proposta)* | Automática | Sim |
| `proposal.write` | Escrever propostas | Automática | Não (próximo módulo) |
| `proposal.submit` | Enviar proposta | Minha aprovação | **Não existe**: o gateway nega |
| `contract.accept` | Aceitar contrato | Minha aprovação | **Não existe**: o gateway nega |
| `project.develop` | Desenvolver projeto contratado | Automática, isolado | Não (futuro) |
| `delivery.send` | Entregar ao cliente | Minha aprovação | **Não existe**: o gateway nega |
| `payment.*` | Receber pagamentos | Eu | **Nunca**: o gateway nega sempre, e não haverá executor |

**Decisão sua:** acrescentar ao `AUTHORIZATION.md` uma seção "Ações de infraestrutura" com as duas linhas marcadas como *proposta* acima (falar com você mesmo no Telegram e renovar credenciais). Não mexi no seu arquivo sem a sua confirmação.

### 3.3 Aprovação vinculada ao conteúdo (requisito 3)
Fica especificado agora e é implementado junto com a **primeira** ação que precisar dela (`proposal.submit`). Nesta fase o gateway já nega qualquer ação desse nível, então não há risco no intervalo.

Invariantes do `ApprovalRequest`, cada um com teste obrigatório:
1. Guarda o **payload canônico** (JSON com chaves ordenadas) e o `content_hash` (SHA-256) do que será executado. Para proposta: texto completo, preço, prazo, anexos e ID do projeto.
2. A mensagem de aprovação no Telegram mostra o **conteúdo integral** (ou anexa um arquivo, se for longo) e os 8 primeiros caracteres do hash.
3. Só vale aprovação vinda do `from_user.id` do dono, em chat privado.
4. **Uso único**: a execução marca `used_at` na mesma transação. Um segundo clique ou um reenvio não executa de novo.
5. **Expiração** (padrão 24 h). Silêncio não aprova.
6. Na execução, o gateway **recalcula** o hash do payload que vai mesmo ser enviado e compara com o aprovado. Se divergir, nega.
7. Qualquer edição do conteúdo invalida o pedido e gera outro.
8. Ação, alvo e hash ficam no `ActionLog`.

Os casos de teste individuais (AP-01 a AP-22) estão no design v2.1, seção 5.6.1.

### 3.4 Isolamento do desenvolvimento futuro (requisito 4)
Agora entram só os requisitos, num ADR. A implementação fica para o módulo de execução. O que muda **já**:
- **Nenhum** container do sistema monta `docker.sock` nem roda privilegiado.
- **Segredos por container**: `web` recebe só o banco; `worker` recebe banco, Anthropic e plataformas; `bot` recebe banco e Telegram.

Requisitos do futuro *sandbox runner*:
- Serviço separado, idealmente em outra VM ou com runtime isolado (gVisor ou equivalente). Ele recebe tarefas pelo banco ou pela fila, nunca por socket do Docker do host.
- Sem *bind mounts* do host. O código entra por clone de um repositório dedicado ao projeto do cliente.
- Nenhum segredo do Freelance Agent nem das contas do usuário.
- Usuário não-root, `cap_drop: ALL`, `no-new-privileges`, *rootfs* somente leitura com `tmpfs` de trabalho, limites de CPU, memória e tempo.
- Rede bloqueada por padrão; liberação só por *allowlist* (registries de pacotes) via proxy.
- Ambiente efêmero, destruído ao fim. A saída é um artefato (diff ou pacote) que segue para "Entregar ao cliente", que exige aprovação.

### 3.5 Conformidade das fontes (requisito 5)
- Cada `Source` ganha `compliance_status` (`pending` | `approved` | `denied`), `compliance_evidence` (link ou e-mail, data) e `terms_version_checked`. **O gateway nega `platform.search` para fontes que não estejam `approved`.**
- **Freelancer.com:** a coleta agendada só liga com permissão por escrito. Sem ela, vale a sua regra original e a fonte fica de fora, restando a entrada manual (opção A).
- **Upwork:** só liga com a chave aprovada **e** uma resposta por escrito da Upwork sobre enviar vagas a um LLM de terceiros só para inferência (sem treino e sem retenção acima de 24 h). Mesmo liberada: nunca no golden set, nunca em calibração, nunca em exemplos *few-shot*.
- **Retenção:** cada fonte tem `content_ttl_hours` (24 h para as duas). O conteúdo fica invisível para a aplicação em 20 h e é apagado por uma limpeza a cada 15 min (em dois processos e na inicialização), com teto efetivo de 24 h. Isso vale para o conteúdo das oportunidades **e** para o texto derivado das avaliações (design v2.1, seção 6.1). Ficam só IDs, URL, notas numéricas, preço, recomendação, estado, feedback e hashes. Entradas manuais não expiram.
- **Golden set:** só com exemplos escritos ou parafraseados por você.

### 3.6 Preço v2 (requisito 6)
O LLM continua estimando só as horas por tarefa. Quem faz a conta é `services/pricing.py`, uma função pura:

```
horas_esforço   = horas_estimadas × (1 + overhead_não_faturável)
líquido_alvo    = horas_esforço × taxa_líquida_alvo × (1 + margem_risco) + custo_de_lance
fator_cadeia    = (1 − taxa_saque_%) × (1 − spread_câmbio_%) × (1 − estimativa_imposto_%)
antes_da_taxa   = líquido_alvo / fator_cadeia + taxa_saque_fixa
bruto           = antes_da_taxa / (1 − taxa_plataforma_%)
                  (se bruto × taxa_% < taxa_mínima → bruto = antes_da_taxa + taxa_mínima)
preço_sugerido  = arredondar_para_cima(bruto, múltiplo)
```

- A **taxa-alvo passa a ser líquida** (`target_net_hourly_usd`), o que você quer receber de fato.
- **Taxas da plataforma** ficam numa tabela `FeeSchedule` por fonte, com `source_url` e `verified_at`. Freelancer.com: 10%, mínimo US$ 5 no fixo. Upwork: % assumido configurável, com faixa de 0% a 15% mostrada, porque o valor real só aparece no momento da proposta. Connects (US$ 0,15 cada) entram em `custo_de_lance`.
- **Custos do usuário** (saque, câmbio, estimativa de imposto, overhead) ficam no `Profile`. O imposto entra só como **estimativa configurável** de carga efetiva, informada por você ou pelo seu contador. Não é apuração tributária, e o sistema não presume que a tributação seja um percentual fixo sobre o recebimento (design v2.1, seção 8.1.1).
- A saída mostra o **detalhamento**: bruto, taxa da plataforma, saque, câmbio, imposto, líquido e líquido por hora efetiva.
- O custo de LLM por avaliação (centavos) é registrado, mas não entra no preço.

**Exemplo** (valores **ilustrativos**, não são recomendação): 55–70 h estimadas, overhead 15%, margem de risco 15%, taxa líquida alvo US$ 30/h, Freelancer.com (10%), saque 1% + US$ 2, câmbio 1,5%, estimativa de carga tributária 6%.
→ Preço sugerido **US$ 2.650–3.400**. Taxa da plataforma US$ 265–340. Líquido estimado ≈ **US$ 2.184–2.803**, cerca de US$ 34,5 por hora de esforço (a margem de risco acima dos US$ 30 cobre estouro de escopo).
Pela fórmula v1, o mesmo projeto sairia por US$ 2.600 brutos, abaixo do mínimo necessário para a meta.

### 3.7 Anthropic (requisito 7)
Verificado na documentação oficial em 18/11/2026:

| Modelo | ID | Entrada | Saída | Escrita cache 5 min | Leitura cache | Mín. cacheável |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | `claude-haiku-5-5` | US$ 0,10/MTok (prompt ≤ 100k) | US$ 0,50/MTok | US$ 0,125/MTok | US$ 0,01/MTok | 512 tokens |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | US$ 2/MTok | US$ 10/MTok | US$ 2,50/MTok | US$ 0,10/MTok | 512 tokens |

Batch API com 50% de desconto (não recomendado agora: aumenta latência e complexidade).

Alterações:
- Tabela de preços nas *settings* com `verified_at`. Um teste falha se `verified_at` tiver mais de 90 dias em produção; antes de implementar, o limite é 30 dias (critério 7 da seção 5).
- **`effort` explícito**: triagem `low`, avaliação completa `medium`. `max_tokens` dimensionado contando o raciocínio. Valores confirmados medindo na Fase 0.
- **Reserva de orçamento antes da chamada**: custo máximo possível = entrada medida + `max_tokens` × preço de saída. Se não couber no teto do dia, a chamada não acontece. Fila de LLM com concorrência 1.
- Limite de tamanho da descrição enviada (ex.: 20 mil caracteres), para nunca cair na faixa de preço acima de 100k tokens.
- Instrução de sistema (papel + rubrica + `PROFILE.md`) acima de 512 tokens, para o cache funcionar.

**Estimativa** (com os preços acima; tokens supostos: 2.500 de sistema em cache, 1.500 de vaga; 600 de saída na triagem e 3.000 na completa):
- Triagem ≈ US$ 0,0005 por vaga.
- Avaliação completa ≈ US$ 0,033 por vaga.
- 100 triagens + 15 completas por dia ≈ **US$ 0,70/dia (~US$ 21/mês)**.
- 300 + 40 por dia ≈ **US$ 1,63/dia (~US$ 49/mês)**.
- **A medição real com contagem de tokens substitui essas suposições na Fase 0.**

### 3.8 Ajustes menores
- Telegram: checar `chat_id` e `from_user.id`; desativar a entrada do bot em grupos no BotFather; documentar a rotação do token.
- Rede: `web` publicado só em `127.0.0.1`; acesso por túnel; `DEBUG=False`; `ALLOWED_HOSTS` restrito.
- Backups do Postgres sem conteúdo de terceiros: `pg_dump` excluindo os dados das tabelas de conteúdo; sem WAL/PITR nem *snapshots* de disco (design v2.1, seção 6.1).

---

## 4. Plano revisado da Fase 0 (v2, histórico)

> Mantido como registro. A referência atual é o checklist da seção 6.

A Fase 0 **não tem código de produto**. Ela produz evidências, decisões e ADRs. As Fases 1 a 4 (fundação, domínio, avaliador com entrada manual, Telegram) **não dependem** das liberações das plataformas e podem andar enquanto as respostas não chegam:
- A Fase 1 começa após 0.1 e 0.10.
- A Fase 3 precisa também de 0.4, 0.6 e 0.7.
- A Fase 4 precisa de 0.5.

Só as Fases 5 e 6 esperam 0.2 e 0.3.

| # | Atividade | Responsável | Entregável | Critério de saída |
|---|---|---|---|---|
| 0.1 | Aprovar as mudanças desta revisão e a seção "Ações de infraestrutura" no `AUTHORIZATION.md` | Você | `AUTHORIZATION.md` atualizado | Aprovado por escrito |
| 0.2 | **Freelancer.com:** registrar o app no portal de desenvolvedores, ler os termos vigentes no cadastro e **pedir permissão por escrito** para polling somente leitura, de uso pessoal, com frequência declarada (ex.: a cada 15 min), retenção ≤ 24 h e sem redistribuição. **Nenhuma chamada automatizada antes da resposta.** | Você (eu redijo o pedido) | E-mail enviado + resposta arquivada | `approved` ou `denied` registrado |
| 0.3 | **Upwork:** confirmar perfil completo; pedir a chave (uso pessoal, escopo de busca de vagas); pedir à Upwork, por escrito, esclarecimento sobre inferência por LLM de terceiros (seções 5.3 e 9.6) | Você (eu redijo o pedido) | Chave + resposta arquivada | `approved` ou `denied` registrado |
| 0.4 | **Anthropic:** criar uma chave dedicada ao projeto, com limite de gasto no Console se disponível; confirmar IDs e preços na data; **medir tokens** do prompt real com a contagem de tokens; testar `effort` e `max_tokens` com 5 exemplos manuais | Eu (com a sua chave) | Tabela de preços com `verified_at` + medições | Custo por avaliação medido e teto diário definido |
| 0.5 | **Telegram:** criar o bot, desativar grupos, anotar `chat_id` e `user_id` | Você | Valores no gerenciador de segredos | Bot responde só ao dono |
| 0.6 | **Parâmetros de preço:** taxa líquida alvo, taxa líquida mínima, orçamento mínimo do fixo, overhead, margem de risco, método de saque e suas taxas, câmbio, **estimativa** de carga tributária (com o seu contador) | Você | Valores + fonte de cada taxa | Todos preenchidos ou explicitamente "não definido" |
| 0.7 | **Golden set:** 30 exemplos escritos ou parafraseados por você, rotulados aplicar / talvez / ignorar, **sem conteúdo da Upwork** | Você | `tests/fixtures/golden.jsonl` | 30 itens rotulados |
| 0.8 | **ADRs:** 001 Central de Ações; 002 Aprovação vinculada ao conteúdo; 003 Retenção e conformidade de fontes; 004 Preço v2; 005 Sandbox de desenvolvimento (futuro) | Eu | `docs/adr/` | Revisados por você |
| 0.9 | **Modelo de ameaças** curto: ativos, atores, fronteiras e os riscos R1–R13 com dono e mitigação | Eu | `docs/threat-model.md` | Todo risco alto com mitigação e teste |
| 0.10 | **Matriz de segredos e rede**: que segredo vai em qual container; portas expostas | Eu | Seção no README | Revisada |
| 0.11 | **Revisão de prontidão** com os critérios da seção 5 | Nós | Checklist assinado | Todos os critérios OK |

**Se as plataformas negarem:** o sistema roda só com entrada manual (opção A). Nada da arquitetura se perde, porque os conectores são plugáveis.

---

## 5. Critérios objetivos de prontidão para implementação

A arquitetura está pronta para implementar quando **todos** forem verdadeiros:

1. **Política:** `AUTHORIZATION.md` aprovado, incluindo as ações de infraestrutura, e cada ação externa do design aparece na tabela da seção 3.2 com nível e linha correspondentes. **Zero ações sem mapeamento.**
2. **Gateway:** o design define a negação por padrão, o `ActionLog`, a regra de importação verificável por ferramenta (`import-linter`) e o teste de consistência `.md` ↔ código.
3. **Aprovação:** os 8 invariantes da seção 3.3 estão escritos, e cada um tem um caso de teste nomeado. ✅ Atendido na v2.2: CT-AP-1 a CT-AP-8, com subcasos AP-01 a AP-22 (design, seção 5.6.1).
4. **Fontes:** cada fonte tem `compliance_status` com evidência datada. Pelo menos uma de duas condições vale: uma fonte automática está `approved`, **ou** você aceitou por escrito começar só com a entrada manual. ✅ **Atendido em 18/11/2026:** você aceitou começar só com a entrada manual (design v2.1, seção 4).
5. **Retenção:** existe uma tabela campo a campo com TTL por fonte, e a rotina de limpeza está especificada (frequência, campos, efeito nos backups). O teto efetivo é de 24 h, incluindo atrasos, medido pela métrica única `content_max_age_hours` (design v2.2, seção 6.1).
6. **Preço:** a fórmula v2 está especificada, e o exemplo é reproduzível por cálculo. Cada parâmetro tem valor **ou** comportamento definido quando falta (preço não calculado). Cada taxa de plataforma tem `source_url` e `verified_at` de no máximo 30 dias.
7. **LLM:** IDs e preços com `verified_at` de no máximo 30 dias; custo por avaliação **medido**, não estimado; `effort`, `max_tokens`, tamanho máximo de entrada e teto diário definidos.
8. **Segurança:** modelo de ameaças com todos os riscos de impacto alto mitigados e com dono; matriz de segredos sem nenhum container com `docker.sock` nem segredo desnecessário; `web` sem exposição pública.
9. **Sandbox:** ADR-005 existe com os requisitos da seção 3.4, mesmo que a implementação seja futura.
10. **Plano:** cada tarefa das Fases 1 a 4 tem critério de aceite testável.
11. **Nenhuma pergunta aberta marcada como bloqueante** no documento de design.

---

## 6. Checklist da Fase 0 (atualizado na v2.3)

> O checklist original da v2.2 foi aprovado com "Fechado!" em 18/11/2026. Esta versão registra as aprovações D1 e D2 e o núcleo de autorização antecipado a pedido do usuário. Os itens concluídos permanecem visíveis para manter o histórico.

Legenda: ☐ pendente · ✅ feito. Fora o núcleo de autorização (N1), **nada aqui inclui código de produto**. Credenciais **nunca** são enviadas pelo chat: elas entram direto no arquivo `.env` ou no gerenciador de segredos do servidor.

### 6.1 O que você precisa fornecer

**Decisões** (detalhes na seção 17 do design):

| # | Item | Bloqueia |
|---|---|---|
| ✅ D1 | Aprovação da arquitetura v2.2 e deste checklist ("Fechado!", 18/11/2026). **Confirme na revisão desta entrega** | — |
| ✅ D2 | `owner.notify` e `platform.oauth_refresh` aprovadas em 18/11/2026, com a ressalva de que não autorizam proposta, contrato, entrega nem pagamento | — |
| ✅ D3 | Máquina do usuário com Docker Compose nas Fases 1 a 4; GitHub privado; GitHub Actions com PostgreSQL 17; VPS reavaliado antes da Fase 5. **Falta:** criar o repositório e ativar a proteção de branch | — |
| ✅ D4 | **Temporário:** alternativa A só enquanto houver apenas entrada manual; reavaliar antes de qualquer conector (não herdada pela Fase 5) | Reavaliação bloqueia a Fase 5 |
| ✅ | Começar só com entrada manual | — |

**Credenciais e contas:**

| # | Item | Bloqueia |
|---|---|---|
| ☐ F1 | Chave da Anthropic **dedicada** ao projeto, com limite de gasto no Console, se disponível | Fase 3 (e a medição 0.4) |
| ☐ F2 | Bot do Telegram criado no BotFather (entrada em grupos desativada), token, e seu `chat_id` e `user_id` | Fase 4 |
| ☐ F3 | Situação da conta na Upwork (completa ou não) | Só Fase 6 |

**Dados:**

| # | Item | Bloqueia |
|---|---|---|
| ☐ F4 | **Obrigatório para concluir a Tarefa 3.1.** Parâmetros de preço em USD, ou "não definido" em cada um: taxa líquida alvo/h, taxa líquida mínima/h, orçamento mínimo de projeto fixo, % não faturável, margem de risco base, arredondamento, método de saque e suas taxas, *spread* de câmbio, **estimativa** de carga tributária (de preferência com o seu contador), teto diário de LLM | Não bloqueia código; sem eles, o preço não é calculado em operação |
| ☐ F5 | Golden set: 30 projetos escritos ou parafraseados por você, rotulados aplicar / talvez / ignorar, sem conteúdo da Upwork | Aceite da Fase 3 (medir a qualidade do avaliador) |

**Plataformas** (não bloqueiam o início):

| # | Item | Bloqueia |
|---|---|---|
| ☐ F6 | Enviar o pedido de permissão ao Freelancer.com (eu redijo) e arquivar a resposta | Só Fase 5 |
| ☐ F7 | Pedir a chave da Upwork e o esclarecimento sobre inferência por LLM (eu redijo) e arquivar a resposta | Só Fase 6 |

### 6.2 O que eu consigo preparar (documentos, sem código de produto)

| # | Item | Depende de |
|---|---|---|
| ✅ P1 | Seção "Ações de infraestrutura" aplicada ao `AUTHORIZATION.md` após a aprovação D2 (**revise o texto**) | — |
| ☐ P2 | ADR-001 Central de Ações; ADR-002 Aprovação vinculada ao conteúdo; ADR-003 Retenção e conformidade de fontes; ADR-004 Preço v2 (com imposto como estimativa); ADR-005 Sandbox de desenvolvimento (futuro); ADR-006 `ActionLog` *append-only* | — |
| ☐ P3 | Modelo de ameaças (`docs/threat-model.md`) com R1–R13, dono e mitigação | — |
| ☐ P4 | Matriz de segredos, papéis do banco (`fa_owner`/`fa_app`) e rede por container | D3 (para fixar host e portas) |
| ☐ P5 | Rascunhos dos pedidos ao Freelancer.com e à Upwork | — |
| ☐ P6 | Formulário dos parâmetros de preço (F4) com explicação de cada campo | — |
| ☐ P7 | Formato do golden set (F5) com 3 exemplos **fictícios** de referência | — |
| ☐ P8 | Rascunho dos textos de `prompts/evaluator.md` e `prompts/hunter.md`, para revisão (texto, não código) | — |
| ✅ P9 | Plano das Fases 1 a 4 **aprovado com 5 ajustes** (18/11/2026), execução por subagentes | — |
| ☐ P10 | Medição real de tokens e custo (item 0.4): exige um script descartável de medição e a sua chave. **Só com a sua autorização explícita**, por envolver código | F1 + sua autorização |
| ✅ N1 | **Antecipado a seu pedido:** registro de permissões + política + lógica pura do `ApprovalRequest` + gateway com auditoria em memória. São 62 testes passando e 1 pulado (AP-12, exige o `ApprovalRequest` persistido em PostgreSQL, que entra com o módulo de propostas); `ruff` limpo. Detalhes no design, seção 5.5.1 | — |

### 6.3 O que bloqueia a implementação

| Para começar… | Precisa de |
|---|---|
| **Qualquer código além do N1** | Sua revisão desta entrega (N1 + `AUTHORIZATION.md`) + plano de implementação (P9) aprovado por você |
| Fase 1: fundação | D3 + matriz de segredos (P4) |
| Fase 2: domínio + Central de Ações | ADRs 001, 002, 003 e 006 (P2). D2 ✅. O N1 já cobre a lógica; falta integrar com Django, PostgreSQL e o `ActionLog` persistido |
| Fase 3: avaliador | F1 + medição (P10) + ADR-004; F5 para o aceite; F4 para ativar o preço em operação |
| Fase 4: Telegram | F2 |
| Fase 5: Freelancer.com | Permissão por escrito (F6) + D4 |
| Fase 6: Upwork | Chave + esclarecimento (F7) + conta (F3) + D4 |

**Caminho crítico para iniciar:** sua revisão desta entrega → P9 aprovado → D3 → Fase 1. As Fases 5 e 6 não estão no caminho crítico.

### 6.4 Alternativas para as decisões D3 e D4

**D3: onde o sistema roda e onde fica o repositório**

| Opção | Como fica | Consequências |
|---|---|---|
| **A. Sua máquina** (Docker Desktop ou Docker no Linux) | Os quatro serviços rodam localmente | Sem custo extra; dados e credenciais ficam com você. O bot e as tarefas param quando o computador dorme ou desliga, então a limpeza não roda nesses períodos (ver D4). Bom para as Fases 1 a 4, só com entrada manual |
| **B. Servidor/VPS pequeno** (Linux + Docker) | Mesmo compose, com acesso por túnel SSH | Ligado 24 h, que é o adequado para o bot e para a retenção a partir da Fase 5. Custo mensal baixo, variável por provedor. Exige manutenção: chaves SSH, firewall, atualizações e **desligar *snapshots* automáticos do volume do banco** (seção 6.1 do design) |
| **C. Plataforma gerenciada** (PaaS e banco gerenciado) | Serviços e banco do provedor | **Não recomendada:** bancos gerenciados costumam ter *backups*/PITR automáticos e restrições de papéis e *triggers* que conflitam com a retenção de 24 h e com o `ActionLog` *append-only* |
| **Recomendação** | **A** durante o desenvolvimento das Fases 1 a 4, e **B** antes de ligar qualquer fonte de terceiros (Fase 5) ou quando quiser o bot 24 h | O compose é o mesmo nas duas; a migração é só implantação |

**Repositório e CI:** repositório Git **privado** com CI que rode um serviço PostgreSQL 17 (por exemplo, GitHub + GitHub Actions; GitLab CI também serve). Segredos nunca vão para o repositório.

**D4: exceção física da retenção (servidor desligado)**

| Opção | Como funciona | Consequências |
|---|---|---|
| **A. Aceitar a exceção documentada** | Com o servidor desligado, os dados ficam em repouso; ao religar, a limpeza roda antes de tudo e o evento vira incidente | Nenhuma complexidade extra. Risco de passar de 24 h em quedas longas. Combinada com D3-A (máquina que dorme), isso aconteceria com frequência depois da Fase 5 |
| **B. Apagamento criptográfico** | O conteúdo de terceiros é cifrado com uma chave que fica **só na memória** e é trocada a cada poucas horas | Ao desligar, a chave some e o conteúdo fica ilegível na hora, o que equivale a apagá-lo. Fecha a exceção. Custa gestão de chave e perda do conteúdo em reinícios (aceitável, pois ele expira de qualquer forma) |
| **C. Não guardar conteúdo de terceiros** | Avaliar na mesma tarefa da coleta e guardar só números | Fecha a exceção, mas perde "Detalhes", reavaliação e fila em caso de teto de orçamento. Muda comportamento; **não recomendada** |
| **Recomendação** | **A agora**: com entrada manual não há conteúdo de terceiros, então D4 não tem efeito nas Fases 1 a 4. **Reavaliar antes da Fase 5**: com D3-B, A tende a bastar; com D3-A, escolher B | Não bloqueia nada do P9 |

## 7. Histórico deste documento

| Versão | Data | Mudança |
|---|---|---|
| Revisão da v1 | 18/11/2026 | Problemas P1–P23, riscos R1–R13, alterações, plano da Fase 0 e critérios de prontidão; design atualizado para v2 |
| Alinhamento v2.1 | 18/11/2026 | Retenção com teto efetivo de 24 h, imposto como estimativa, testes de aprovação, imutabilidade do `ActionLog`, início com entrada manual; critério 4 atendido |
| Alinhamento v2.2 | 18/11/2026 | Métrica única `content_max_age_hours`; CT-AP-1 a CT-AP-8; *append-only* explícito; checklist da Fase 0 (seção 6); critério 3 atendido |
| Alinhamento v2.3 | 18/11/2026 | D1 e D2 aprovados; `AUTHORIZATION.md` com as ações de infraestrutura; núcleo de autorização (N1) e testes implementados; checklist atualizado |
| P9 e alternativas | 18/11/2026 | Plano P9 (`docs/superpowers/plans/2026-11-18-fases-1-a-4-entrada-manual.md`) para sua aprovação; alternativas de D3 e D4 (seção 6.4); AP-12 reclassificado para o módulo de propostas |
| Aprovação do P9 | 18/11/2026 | D3 e D4 (temporário) decididos; P9 aprovado com ajustes; conectores sem executor; F4 obrigatório para preço; AP-12 obrigatório antes das propostas |

## Fontes consultadas (18/11/2026)
- Freelancer.com — User Agreement (seção 33, automated access): https://www.freelancer.com/about/terms
- Freelancer.com — API Terms & Conditions: https://www.freelancer.com/about/apiterms
- Freelancer.com — Fees and Charges: https://www.freelancer.com/feesandcharges
- Upwork — API & MCP Terms of Use (PDF): https://upwork.pactsafe.io/versions/6a7de807acdc47dbc84c4892.pdf
- Upwork — Request an API key: https://support.upwork.com/hc/en-us/articles/115015857647
- Upwork — Is Upwork free? (Connects): https://www.upwork.com/resources/is-upwork-free
- Upwork — MCP: https://www.upwork.com/ai/mcp
- Anthropic — Models overview: https://platform.claude.com/docs/en/models/overview
- Anthropic — Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Anthropic — Sonnet 5.5 migration guide (cache read US$ 0,10): https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide
- Anthropic — Haiku 5.5: https://platform.claude.com/docs/en/models/haiku-5-5/overview
- Anthropic — Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Procrastinate — PyPI: https://pypi.org/project/procrastinate/
