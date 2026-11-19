# Matriz de segredos, papéis do banco e rede (P4)

**Status:** preparada pelo controlador em 18/11/2026 para a Tarefa 1.2; **sujeita à revisão do usuário** (arquivo de critério de segurança).
**Ambiente (D3):** máquina do usuário, Docker Compose, Fases 1 a 4 (só entrada manual).

## Variáveis por serviço

| Variável | db | migrate | web | worker | bot |
|---|---|---|---|---|---|
| `POSTGRES_PASSWORD` (superusuário) | ✅ | — | — | — | — |
| `FA_OWNER_PASSWORD`, `FA_APP_PASSWORD` (criação dos papéis no primeiro boot) | ✅ | — | — | — | — |
| `DATABASE_URL` com papel **`fa_owner`** | — | ✅ | — | — | — |
| `DATABASE_URL` com papel **`fa_app`** | — | — | ✅ | ✅ | ✅ |
| `DJANGO_SECRET_KEY` | — | ✅ | ✅ | ✅ | ✅ |
| `DJANGO_ALLOWED_HOSTS` | — | — | ✅ | — | — |
| `PROFILE_MD_PATH` (arquivo montado somente leitura) | — | ✅ | ✅ | ✅ | ✅ |
| `ANTHROPIC_API_KEY` | — | — | — | ✅ | — |
| `EVAL_TRIAGE_MODEL`, `EVAL_DEEP_MODEL`, `EVAL_*_EFFORT`, `EVAL_*_MAX_TOKENS`, `LLM_MAX_DESCRIPTION_CHARS` | — | — | — | ✅ | — |
| `FIELD_ENCRYPTION_KEY` (tokens OAuth) | — | — | — | ✅ | — |
| `TELEGRAM_BOT_TOKEN` | — | — | — | ✅ ¹ | ✅ |
| `TELEGRAM_OWNER_CHAT_ID`, `TELEGRAM_OWNER_USER_ID` | — | — | — | ✅ ¹ | ✅ |
| `TZ=America/Manaus` | ✅ | ✅ | ✅ | ✅ | ✅ |
| Credenciais do Freelancer.com e da Upwork | — | — | — | — | — |

¹ **Correção em relação ao design (seção 5.2), para sua revisão:** o design dá ao `worker` saída para o Telegram, porque ele envia as notificações via `owner.notify`, mas não lista o token do bot entre os segredos dele. Sem o token, o `worker` não consegue notificar. A matriz inclui `TELEGRAM_BOT_TOKEN` e `TELEGRAM_OWNER_CHAT_ID` no `worker`; a ação continua limitada ao chat do dono pela Central (AZ-07). A alternativa seria só o `bot` enviar, com o `worker` enfileirando as mensagens, ao custo de mais um consumidor de fila.

**Credenciais de plataformas:** nenhuma nas Fases 1 a 4. Os conectores estão sem executor (AZ-16) e nenhum container recebe essas credenciais.

## Rede e portas

| Serviço | Porta publicada | Saída de rede necessária |
|---|---|---|
| `db` | nenhuma (só a rede interna do compose) | nenhuma |
| `migrate` | nenhuma | `db` |
| `web` | `127.0.0.1:8000` | `db` |
| `worker` | nenhuma | `db`, `api.anthropic.com`, `api.telegram.org` |
| `bot` | nenhuma | `db`, `api.telegram.org` |

## Regras fixas

- Nenhum serviço monta `/var/run/docker.sock`, nenhum roda como `privileged`, e todos usam usuário não-root (exceto a imagem oficial do `db`, que já roda o servidor como `postgres`).
- `PROFILE.md` é montado somente leitura (`:ro`) em `migrate`, `web`, `worker` e `bot`.
- Arquivos `env/<serviço>.env` ficam fora do Git; só os `*.env.example` são versionados, sem valores reais.
