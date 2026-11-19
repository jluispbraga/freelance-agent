# ADR-006: `ActionLog` append-only garantido no banco de dados

**Status:** Proposto, revisão 3 (decisões do usuário de 2026-11-19 aprovadas e incorporadas; **implementação ainda não liberada**, pois falta aprovar as alterações no plano P9)
**Data:** 2026-11-19
**Fontes:** design v2.4 §5.8, §6 (`ActionLog`), §12, §13, §16 (decisões 16, 28, 29); revisão de 18/11/2026 §3.1; plano P9 (Tarefas 1.3, 2.4, 2.5, 4.4); `docker/db/init-roles.sql`; `app/migrations/0001_roles.py`; `scripts/test_db_setup.sh`; `tests/conftest.py`; decisões do usuário P-001.3, P-006.1 a P-006.3 e P-006.5.

## Contexto

A Central de Ações (ADR-001) grava toda tentativa, permitida ou negada, no `ActionLog`. Esse registro só tem valor de auditoria se **a aplicação não conseguir alterá-lo nem apagá-lo**, por qualquer caminho (ORM, SQL bruto, Admin, API, bug). Uma garantia apenas em código Python seria contornável pelo mesmo código que se quer auditar.

Já existem os papéis do PostgreSQL (Tarefa 1.3): `fa_owner` (dono do banco e do schema, usado só pelo `migrate`) e `fa_app` (`NOSUPERUSER NOCREATEROLE NOBYPASSRLS`, usado por `web`, `worker` e `bot`). A migração `0001_roles` concede a `fa_app` `SELECT, INSERT, UPDATE, DELETE` em tabelas futuras do dono, via `ALTER DEFAULT PRIVILEGES`, e `USAGE, SELECT` em sequências. O `AuditEntry` atual mistura, no campo `decision`, a decisão de autorização, o resultado da execução e os desfechos do ciclo de aprovação.

## Problema

Como tornar o `ActionLog` imutável **no banco** para o papel de execução, proteger também contra erro operacional do dono, tornar a adulteração por superusuário **detectável** com um protocolo de hash estável no tempo, e separar com clareza autorização, evento e resultado?

## Alternativas consideradas

As fontes registram a decisão por camadas, não uma comparação formal de alternativas. O que está documentado:
- **Só camada Django** (`save()`/`delete()` bloqueados): mantida **apenas como falha antecipada**; o design afirma que ela "não é a garantia".
- **Só permissões** (papéis): garantia principal, sem proteger contra erro operacional de `fa_owner`; por isso complementada por *triggers*.
- **Detecção por cadeia de hashes**: não impede quem é superusuário no servidor, mas torna a adulteração **detectável**.

Para o `AuditEntry` (decisão do usuário) havia duas formas de separar `decision` de `result`; ver **Decisão 8**, opções A e B.

Outras alternativas (armazenamento externo imutável, tabela particionada) não aparecem nas fontes e não são propostas aqui.

## Decisão

Defesa em camadas (design §5.8), mais as decisões do usuário de 19/11/2026.

1. **Separação de papéis (garantia principal).** `fa_owner` é dono do schema e da tabela `action_log`. `fa_app` recebe sobre `action_log` **apenas** `SELECT` e `INSERT`, mais `USAGE` na sequência do `id`; `UPDATE`, `DELETE`, `TRUNCATE` e `REFERENCES` são revogados **explicitamente** na migração que cria a tabela, exceção ao `ALTER DEFAULT PRIVILEGES` das demais tabelas. Como `fa_app` não é dono, não concede permissões a si mesmo, não altera a tabela e não desativa *triggers*; também não é superusuário, não tem `CREATEROLE` nem `BYPASSRLS`, não é membro de `fa_owner` e **`PUBLIC` não tem privilégio algum** sobre a tabela.
2. ***Triggers* (defesa em profundidade):** `BEFORE UPDATE OR DELETE … FOR EACH ROW` e `BEFORE TRUNCATE … FOR EACH STATEMENT` lançam exceção, inclusive para `fa_owner`. Contornar exigiria desativar o *trigger* explicitamente.
3. **Sem chaves estrangeiras.** `target`, `approval_id` e `correlation_id` são valores simples. Apagar uma oportunidade ou um pedido de aprovação, ou a limpeza de conteúdo (ADR-003), nunca altera o `ActionLog`.
4. **Camada Django (falha cedo, não é a garantia):** `save()` em registro existente e `delete()` lançam exceção; o *queryset* bloqueia `update()` e `delete()`; Admin somente leitura; API só com `GET`.
5. **Conteúdo do log.** Só identificadores, hashes, decisões, motivos e erros **sem conteúdo bruto de terceiros e sem resposta da Anthropic**. Valores de `event`, `decision` e `result` pertencem a vocabulários fechados. O retorno do executor **nunca** é gravado. **`reason`** é o motivo da **decisão de autorização**; **`error`** é uma descrição **sanitizada e truncada** de uma **falha de execução** (decisão do usuário). Nunca se grava *stack trace*, token, resposta da Anthropic, conteúdo de oportunidade nem dado sensível. Na Fase 2, `error` é só o nome do tipo da exceção (como o gateway já faz); qualquer descrição mais rica depende de um sanitizador definido e testado (P-006.13). Por isso o log pode ser permanente sem violar a retenção (ADR-003).

### 6. Campos do modelo persistido

Design §6 (`id`, `created_at`, `action`, `target`, `payload_hash`, `decision`, `reason`, `approval_id`, `result`, `duration_ms`, `error`, `prev_hash`, `row_hash`) **mais**:
- `event` (decisão do usuário): preserva os eventos que o `AuditEntry` já tem: `created`, `callback`, `expired`, `superseded`, `authorization`, `executed`.
- `correlation_id` (UUID, **aprovado**; ver ADR-001 decisão 9): gerado por tentativa de execução, persistido em M1 antes do executor, **com índice e sem unicidade**.
- `chain_version` (smallint): versão do protocolo de hash da linha (decisão 9).
- Evento novo `reconciled` (**aprovado**, decisão 10).
- Restrições: `UNIQUE(prev_hash)` (**aprovada**), para que cada linha tenha no máximo um sucessor e a cadeia permaneça linear; `NOT NULL` em `prev_hash` e `row_hash`; sem chaves estrangeiras.

### 7. Semântica de `event`, `decision` e `result` (decisão do usuário)

- `event`: o que aconteceu.
- `decision`: a decisão de **autorização** associada ao evento.
- `result`: o resultado **resumido** da execução, em vocabulário fechado: `ok`, `error` ou `unknown`. Nulo quando não houve execução.

**Mapeamento aprovado.** `decision` representa **só** a decisão de autorização; `result` representa **só** o resultado da execução; os dois conceitos não se misturam no modelo persistido. Os eventos do ciclo de aprovação preservam o vocabulário atual.

| `event` | `decision` | `result` | Observação |
|---|---|---|---|
| `authorization` | `allowed` \| `denied` | nulo | M1 |
| `executed` | `allowed` | `ok` \| `error` | M3; a linha herda a autorização que a permitiu |
| `reconciled` | `allowed` | `unknown` | autorização sem resultado conclusivo |
| `created`, `callback`, `expired`, `superseded` | vocabulário atual: `recorded`, `approved`, `rejected`, `denied`, `ignored`, `expired` | nulo | ciclo de aprovação (ADR-002) |

**Tradução feita pela camada de persistência** a partir do `AuditEntry` em memória:

| `AuditEntry` em memória | Persistido |
|---|---|
| `event=executed`, `decision=ok` | `decision=allowed`, `result=ok` |
| `event=executed`, `decision=error` | `decision=allowed`, `result=error` (`error` preenchido) |
| `event=authorization`, `decision=allowed` / `denied` | idem, `result` nulo |
| execução autorizada sem resultado conclusivo (produzida pela conciliação) | `event=reconciled`, `decision=allowed`, `result=unknown` |
| eventos de aprovação | inalterados |

### 8. Alteração de `audit.py` (**Opção A aprovada**; ainda **não implementada**)

**Opção A (aprovada):** acrescentar campos **opcionais com valor padrão** ao `AuditEntry` e manter intactos os valores atuais de `event` e `decision` em memória.

```python
@dataclass(frozen=True)
class AuditEntry:
    event: str
    action: str
    target: str
    approval_id: str | None
    payload_hash: str
    decision: str
    reason: str
    at: datetime
    # novos, opcionais, preenchidos pela camada de persistência / gateway em dois passos:
    result: str | None = None          # ok | error | unknown (em memória, só para eventos novos)
    error: str | None = None           # descrição sanitizada de falha de execução; nunca stack trace
    duration_ms: int | None = None
    correlation_id: str | None = None
```

A tradução para o modelo persistido (por exemplo, `decision="ok"` do evento `executed` vira `decision="allowed"`, `result="ok"`) fica em `app/actions/persistence.py`. **Vantagem:** os testes protegidos (`test_approval_invariants.py` linhas 204 e 346, que esperam `decision == "error"` e `"ok"`) e o AP-21 continuam válidos **sem alteração**.

**Opção B (não adotada):** mover `ok`/`error` de `decision` para `result` já dentro do `AuditEntry`. É mais limpa, mas **quebra dois asserts em arquivo protegido** e exige a sua aprovação explícita para editar `tests/actions/test_approval_invariants.py`.

### 9. Protocolo da cadeia de hashes, versão 1 (decisão do usuário)

Normativo; toda alteração de campos ou de serialização cria uma **nova versão** e **nunca** reinterpreta linhas antigas.

- **Algoritmo:** SHA-256, resultado em hexadecimal minúsculo de 64 caracteres.
- **Entrada:** um objeto JSON com os campos abaixo, serializado em **JSON canônico UTF-8**: chaves ordenadas, sem espaços (`separators=(",", ":")`), `ensure_ascii=False`, `allow_nan=False`. **Valores nulos aparecem explicitamente** como `null`.
- **Campos incluídos:** `protocol` (inteiro, `1`), `prev_hash`, `created_at`, `event`, `action`, `target`, `payload_hash`, `decision`, `reason`, `approval_id`, `correlation_id`, `result`, `duration_ms`, `error`. Ou seja, **todos os campos persistidos relevantes, incluindo `prev_hash`**.
- **Campos excluídos:** `row_hash` (é o resultado) e `id` (valor da sequência, atribuído pelo banco e com lacunas em inserções abortadas; a ordem da cadeia vem de `prev_hash`). **Exclusão do `id` aprovada**, com a condição de que **todos os demais campos persistidos relevantes estejam cobertos**: o teste `test_row_hash_cobre_todos_os_campos_e_o_prev_hash` falha se uma coluna nova do modelo não for incluída no protocolo ou explicitamente excluída com justificativa.
- **Datas:** UTC, ISO 8601 em um único formato fixo, `YYYY-MM-DDTHH:MM:SS.ffffffZ` (sempre seis casas decimais e o sufixo `Z`). `created_at` é definido pela aplicação **depois** de obtido o *lock* e gravado exatamente com esse valor (microssegundos, igual à precisão do `timestamptz`).
- **Outros tipos:** UUID em minúsculas com hífens; `duration_ms` inteiro; hashes em hexadecimal minúsculo.
- **Hash inicial (`prev_hash` da primeira linha):** 64 zeros (`"0" * 64`).
- **Versionamento:** a coluna `chain_version` guarda a versão da linha e o objeto hasheado a repete em `protocol`. A cadeia continua entre versões: `prev_hash` é sempre o `row_hash` da linha anterior. O verificador mantém uma implementação por versão e usa a da linha.
- **Vetores de teste (obrigatórios):** o protocolo é acompanhado de vetores fixos (entrada → JSON canônico → hash esperado) em testes, para que qualquer mudança acidental de serialização seja detectada. Os valores serão gerados na implementação e **revisados por você** junto com a Tarefa 2.4.

### 10. Inserção serializada e *lock* (decisão do usuário)

- Cada inserção roda numa **transação própria e curta**: `BEGIN` → `pg_advisory_xact_lock(ACTIONLOG_ADVISORY_LOCK_KEY)` → ler o `row_hash` da última linha (ou os 64 zeros) → definir `created_at` → calcular `row_hash` → `INSERT` → `COMMIT`. O *lock* é liberado no fim da transação, o que serializa a inserção **dentro da transação**.
- **Chave fixa, documentada e exclusiva:** `ACTIONLOG_ADVISORY_LOCK_KEY = 4296953195189417418`, derivada de forma reproduzível como os 8 primeiros bytes de SHA-256(`"freelance-agent:actionlog:v1"`) lidos como inteiro de 64 bits com sinal. Uma constante nomeada em um único módulo; um teste verifica que nenhum outro código usa a mesma chave nem chama `pg_advisory_*` sobre ela.
- **Independência da transação de negócio (alias aprovado):** o `ActionLog` usa uma **conexão separada** (alias de banco dedicado, com as credenciais de `fa_app`) para que gravar a linha nunca participe da transação de negócio do chamador. Em Django, um `atomic()` aninhado na mesma conexão seria apenas um *savepoint* e seria desfeito junto. `ActionLog.append` falha se for chamado dentro de um bloco atômico da própria conexão. **Declarar o alias não basta:** os testes precisam **provar** (1) que o commit do `ActionLog` sobrevive ao rollback da transação de negócio e (2) que M1 foi confirmado **antes** da chamada ao executor, observado por uma terceira conexão dentro do executor falso.
- **Conciliação (aprovada):** rotina periódica do `worker`. Localiza linhas `authorization`/`allowed` sem linha `executed` de mesmo `correlation_id` depois do **timeout configurado do executor + 2 minutos**, e acrescenta `reconciled`/`allowed`/`unknown`, reutilizando o `correlation_id`. É idempotente, nunca reexecuta e **não** afirma qual foi o resultado real.

### 11. Verificação de privilégios e conexões nos testes (decisão do usuário)

Os testes abrem **conexões reais e separadas**, cada uma com o papel correto, e **verificam explicitamente** com quem estão conectados:

| Conexão | Papel | Uso |
|---|---|---|
| `como_dono` | `fa_owner` (não superusuário) | Preparar o banco e rodar migrações; teste de que o *trigger* bloqueia `UPDATE`/`DELETE`/`TRUNCATE` até para o dono |
| `como_app` | `fa_app` | Os testes de imutabilidade e a inserção legítima |
| `como_superusuario` | superusuário (variável `DATABASE_URL_SUPER`, **só ambiente de teste**) | **Apenas** o teste controlado de adulteração da cadeia (desativa o *trigger*, altera a linha, **restaura o *trigger* no `finally`**) |

**Restrição da credencial `DATABASE_URL_SUPER` (aprovada só para testes e CI):** nunca é disponibilizada a containers de execução. Garantias testáveis: `test_database_url_super_nao_aparece_em_nenhum_servico_do_compose` (em `tests/infra/test_compose_policy.py`, não protegido), `test_settings_nao_leem_database_url_super` e verificação de que os `env/*.env.example` não a contêm. Ela é lida **só** pela fixture do teste de adulteração; sem ela, esse teste é pulado localmente e **falha** com `REQUIRE_POSTGRES=1`.

Toda fixture afirma, antes dos testes:
- `SELECT current_user, session_user` devolve o papel esperado;
- `pg_roles`: `fa_app` e `fa_owner` com `rolsuper`, `rolcreaterole` e `rolbypassrls` falsos (no teste de adulteração, afirma-se o inverso para o superusuário);
- privilégios **efetivos** de `fa_app` sobre `action_log` por `has_table_privilege`: `SELECT` e `INSERT` verdadeiros; `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES` e `TRIGGER` falsos; `has_sequence_privilege(... 'USAGE')` verdadeiro;
- `relowner` = `fa_owner`; `pg_has_role('fa_app','fa_owner','MEMBER')` falso; `relacl` sem entrada para `PUBLIC`.

Os sete testes do design §5.8 permanecem, agora com essas conexões:
`test_app_nao_consegue_update_no_actionlog`, `..._delete_...`, `..._truncate_...`, `test_app_nao_consegue_alter_table_nem_disable_trigger` (todos como `fa_app`); `test_trigger_bloqueia_update_mesmo_como_owner` (como `fa_owner`); `test_delete_de_oportunidade_nao_afeta_actionlog`; `test_verify_action_log_detecta_linha_alterada` (único com superusuário).

Testes novos decorrentes das decisões: `test_prev_hash_e_unico_e_impede_bifurcacao` (segunda linha com o mesmo `prev_hash` é rejeitada), `test_decision_e_result_nao_se_misturam` (`decision` ∈ vocabulário de autorização/aprovação; `result` ∈ {`ok`,`error`,`unknown`} ou nulo), `test_error_nao_guarda_stack_trace_nem_conteudo`, `test_conexoes_de_teste_tem_papel_e_privilegios_esperados`, `test_nenhum_papel_alem_do_superusuario_adultera_linha`, `test_cadeia_comeca_com_64_zeros`, `test_row_hash_cobre_todos_os_campos_e_o_prev_hash` (alterar qualquer campo muda o hash), `test_serializacao_canonica_v1_vetores_fixos`, `test_insercoes_concorrentes_nao_bifurcam_a_cadeia`, `test_lock_exclusivo_do_actionlog`, `test_append_em_transacao_independente_sobrevive_ao_rollback_do_chamador`, `test_nenhuma_coluna_guarda_conteudo_bruto` (marcador no payload e no retorno do executor nunca aparece no banco), `test_verify_chain_aceita_versoes_mistas`.

## Consequências

**Positivas**
- A imutabilidade não depende de disciplina do código: vale para ORM, SQL bruto, Admin, API e bugs.
- Erro operacional de `fa_owner` também é barrado pelos *triggers*.
- Adulteração por superusuário fica detectável e ancorada fora do servidor (Telegram, Tarefa 4.4).
- Protocolo versionado: a verificação futura não quebra com mudanças de formato.
- Separar `event`, `decision` e `result` torna as consultas de auditoria inequívocas.

**Negativas**
- Correções de dados no log são impossíveis por desenho; erros viram novas linhas.
- Inserções serializadas por *advisory lock* (volume baixo, aceito no design).
- Exige um segundo alias/conexão de banco e uma credencial de superusuário nos testes (nunca em um container de execução).
- A migração do `action_log`, `audit.py` e `gateway.py` são arquivos de segurança com alteração controlada e exigem revisão independente.

**Neutras**
- A cadeia não impede quem tem superusuário no servidor; apenas o torna detectável.
- O PostgreSQL 17 é garantido no CI e no compose; localmente os testes podem rodar em 16. Nada aqui depende de recurso exclusivo do 17.
- `scripts/test_db_setup.sh` concede `CREATEDB` a `fa_owner` **só para o pytest-django criar o banco de teste**; isso não o torna superusuário, e os testes afirmam `rolsuper = false`.

## Impactos em testes e interfaces

| Item | Impacto |
|---|---|
| `AuditEntry` | Quatro campos opcionais novos (opção A); nenhum teste protegido muda. |
| `ActionLog.append(entry)` | Calcula `prev_hash`/`row_hash` sob *lock*, em conexão independente; define `created_at` e `chain_version`. |
| `verify_chain()` | Devolve `VerifyResult(ok, first_bad_id)`; implementa o protocolo v1 e aceita versões mistas. |
| Migração do `action_log` | Colunas `event`, `correlation_id`, `chain_version`; `REVOKE` explícito; *triggers*; sem FKs. |
| `app/config/settings.py` | Novo alias de banco para o `ActionLog` (credenciais de `fa_app`). |
| Ambiente de teste | `DATABASE_URL` (dono), `DATABASE_URL_APP` (app) já existem; **nova** `DATABASE_URL_SUPER`, só para testes e CI, incluída em `scripts/test_db_setup.sh`. Nenhum container de execução a recebe. |
| `tests/conftest.py` | Hoje só exige as duas URLs; passará a conferir a terceira para os testes que a usam. |
| Plano P9 (protegido), Tarefa 2.4 | Acrescentar os testes novos listados acima e o campo `event`: **requer sua aprovação**. |

## Pendências (não decididas; não assumidas aqui)

| # | Questão | Situação |
|---|---|---|
| P-006.1 | Campos canônicos do `row_hash` e formato de datas | **Resolvida e aprovada** (decisão 9). |
| P-006.2 | `prev_hash` inicial | **Resolvida**: 64 zeros. |
| P-006.3 | Chave e escopo do *advisory lock* e rollback | **Resolvida** (decisão 10). |
| P-006.4 | Correspondência `AuditEntry` ↔ colunas | **Resolvida e aprovada** (decisões 6 a 8; Opção A). |
| P-006.5 | Conexões de teste como `fa_app` | **Resolvida** (decisão 11). |
| P-006.6 | Gatilho do `verify_action_log` diário e formato da mensagem-âncora | Aberta; Tarefa 4.4. |
| P-006.7 | O que fazer, além de alertar, ao detectar divergência | Aberta; Tarefa 4.4. |
| P-006.8 | Vocabulário de `decision` nos eventos de aprovação | **Aprovada**: preservado. |
| P-006.9 | `id` fora do hash e `UNIQUE(prev_hash)` | **Aprovada**. |
| P-006.10 | Alias de conexão independente | **Aprovada**, com prova obrigatória por testes (decisão 10). |
| P-006.11 | Conciliação e evento `reconciled` | **Aprovada**: timeout do executor + 2 min; `worker`; sem reexecução. |
| P-006.12 | `error` separada de `reason` | **Aprovada**: `reason` = decisão de autorização; `error` = falha de execução, sanitizada. |
| P-006.13 | **Regra de sanitização e tamanho máximo de `error`** quando for além do nome do tipo da exceção (ex.: mensagem do erro). Mensagens de exceção podem conter conteúdo de terceiros. | Aberta. Na Fase 2, `error` = nome do tipo, o que dispensa o sanitizador. |

## Referências
- Design §5.8, §6, §12, §13
- Plano P9: Tarefas 1.3, 2.4, 2.5, 4.4
- `docker/db/init-roles.sql`; `app/migrations/0001_roles.py`; `scripts/test_db_setup.sh`; `tests/conftest.py`; `tests/db/test_roles.py`
- Relacionados: ADR-001, ADR-003
