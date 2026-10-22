# clinic-manager

Assistente de WhatsApp para um consultório odontológico: atende pacientes, consulta a agenda da dentista e
marca, remarca e cancela consultas seguindo regras fixas — **sem inventar horário, preço ou informação clínica**.

- **Agenda plugável:** Google Agenda, Outlook/Microsoft 365, iCloud/CalDAV (ou fake para desenvolvimento).
- **LLM de qualquer fornecedor** via PydanticAI: OpenRouter, Anthropic, Google, OpenAI… trocando uma variável.
- **Harness determinístico:** o LLM só conversa; horários vêm do código, toda escrita exige proposta + "sim" e é
  revalidada na agenda, dúvidas clínicas vão direto para a dentista sem passar pelo LLM.
- **Extensível:** *capabilities* (lista de espera, recall de 6 meses, pós-consulta…), *skills* em markdown,
  *human-in-the-loop* (handoff de conversa e aprovação de encaixes pela dentista).

## Começando
```bash
uv sync --all-extras
uv run pytest                                   # ~160 testes, sem rede
uv run clinic chat --model test                 # conversa no terminal com um modelo roteirizado
```

Com um LLM de verdade (agenda, banco e WhatsApp continuam falsos — nada é enviado a ninguém):
```bash
export OPENROUTER_API_KEY=...                   # ou ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY
uv run clinic chat --model openrouter:<modelo>
uv run clinic eval --model openrouter:<modelo>  # mede o modelo nos cenários de evals/
```
> Modelos gratuitos podem usar os prompts para treino: use **apenas dados fictícios** com eles.

No chat, `/doutora #ok 1` simula a dentista aprovando um pedido, `/botao CONFIRM:<código>` simula o botão de um
lembrete e `/job daily` roda os jobs agendados.

## Como funciona
```
WhatsApp ──► /webhook ──► Orchestrator (determinístico)
                            ├─ dedupe, comandos da dentista (#ok, #nao, #bot, #msg), SAIR/VOLTAR, botões
                            ├─ estado da conversa (bot | human | waiting_approval)
                            ├─ guard clínico ──► handoff (sem LLM)
                            └─ agente PydanticAI ── tools das capabilities ── PolicyEnforcer (Allow/Deny/Aprovação)
                                    │                         │
                                    ▼                         ▼
                           validadores de saída        agenda (Google/Outlook/CalDAV) + Firestore
Cloud Scheduler ──► /jobs/daily (lembretes, recall, pós-consulta, limpeza LGPD)
```
Detalhes e regras do projeto: [`CLAUDE.md`](CLAUDE.md). Decisões: [`docs/adr/`](docs/adr/).

## Configuração
- Regras da clínica: [`config/clinic.yaml`](config/clinic.yaml) (valores de **EXEMPLO** até a dentista enviar os reais).
- Templates do WhatsApp: [`config/templates.yaml`](config/templates.yaml).
- Conversa: [`skills/`](skills/).
- Variáveis de ambiente: [`.env.example`](.env.example).
- Deploy no Google Cloud (Cloud Run + Firestore + Scheduler) e como conectar cada agenda: [`deploy/README.md`](deploy/README.md).

## Status
| Parte | Situação |
|---|---|
| Motor de horários, harness, agente, capabilities, skills, webhook, jobs | Implementado e coberto por testes |
| Google Agenda, Microsoft Graph | Testados contra APIs simuladas (`httpx.MockTransport`); falta teste com conta real |
| CalDAV | Conversões testadas; falta teste com servidor real |
| Firestore | Implementado e tipado; suíte de contrato pronta para o emulador (`FIRESTORE_EMULATOR_HOST`) |
| WhatsApp Cloud API | Cliente e webhook testados com payloads simulados; falta número real |
| Receitas | Só a interface (ADR 0006) |

Contribuindo: [`CONTRIBUTING.md`](CONTRIBUTING.md).
