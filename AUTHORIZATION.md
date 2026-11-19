# Política de Autorização

Define o que o Freelance Agent pode fazer sozinho e o que depende do freelancer.
Vale para **todos** os módulos, atuais e futuros. Nenhum código pode executar uma ação
com nível mais permissivo do que o definido aqui.

| Ação | Autorização |
|---|---|
| Analisar oportunidades | Automática |
| Classificar projetos | Automática |
| Calcular preço | Automática |
| Escrever propostas | Automática |
| Enviar proposta | Minha aprovação |
| Aceitar contrato | Minha aprovação |
| Desenvolver projeto contratado | Automática, em ambiente isolado |
| Entregar ao cliente | Minha aprovação |
| Receber pagamentos | Eu |

## Ações de infraestrutura

Aprovadas pelo freelancer em 18/11/2026. Ações auxiliares que não aparecem na tabela
principal, mas são ações externas e por isso também passam pela Central de Ações.

| Ação | Identificador | Autorização | Limites |
|---|---|---|---|
| Notificar o freelancer | `owner.notify` | Automática | Envia mensagens e notificações **exclusivamente** ao freelancer, no chat privado dele no Telegram. Nenhum outro destinatário. |
| Renovar credenciais OAuth | `platform.oauth_refresh` | Automática | Renova credenciais OAuth das plataformas **sem ampliar permissões** (escopos iguais ou menores que os já concedidos) e **sem executar nenhuma outra ação**. |

Estas aprovações **não autorizam** envio automático de propostas, aceitação de contratos,
entrega de projetos ou recebimento de pagamentos. A tabela principal continua valendo
integralmente.

## Níveis

- **Automática**: o sistema executa sem perguntar. O resultado fica registrado e visível.
- **Automática, em ambiente isolado**: o sistema executa sozinho, mas apenas dentro de um
  ambiente isolado (container próprio, sem acesso a segredos de produção nem às contas
  do freelancer, com rede restrita).
- **Minha aprovação**: o sistema prepara tudo e pede aprovação explícita no Telegram.
  Sem um "aprovar" registrado, a ação não acontece. Silêncio não é aprovação.
- **Eu**: o sistema não executa, não automatiza e não guarda credenciais para essa ação.

## Regras complementares (do PROFILE.md)

- Nunca inventar experiência profissional.
- Nunca declarar skills não demonstradas.
- Nunca aceitar contratos automaticamente.
- Nunca enviar propostas sem aprovação.
