# Assistente de agendamento para consultório odontológico

## Contexto
Consultório de uma dentista autônoma (tia de um amigo do Artur). Com o aumento de pacientes, ela não consegue
organizar a agenda, encaixar pacientes nem responder a tempo no WhatsApp. Este projeto é um assistente que
atende pacientes pelo WhatsApp, consulta a agenda dela e marca, remarca e cancela consultas seguindo regras fixas.

Objetivos, em ordem de prioridade:
1. **Zero alucinação.** O bot nunca inventa horário, preço, procedimento ou informação clínica.
2. **Custo mínimo.** A hospedagem deve caber no free tier; o custo variável vem do LLM e dos templates do WhatsApp.
3. **Simplicidade.** É um MVP para uma única dentista, sem multi-tenant e sem painel admin por enquanto.
4. **Extensível como um harness.** Novas capacidades entram como *capabilities* e *skills*, sem mexer no núcleo,
   e nunca enfraquecem as garantias acima.

Fora do escopo agora: prontuário, pagamentos, multi-clínica, painel web, conteúdo clínico de qualquer tipo,
geração de receitas (ver ADR 0006: só a *entrega* de receitas assinadas pela dentista, no futuro).

## Stack
- Python 3.12, gerenciado com `uv`
- FastAPI (webhook e endpoints de jobs)
- **PydanticAI como wrapper de LLM, agnóstico de fornecedor.** O modelo vem de `AGENT_MODEL` no formato
  `provedor:modelo` (`openrouter:…`, `anthropic:…`, `google-gla:…`, `openai:…`), com `AGENT_FALLBACK_MODELS`
  opcional. Nenhum código fora de `app/agent/model.py` sabe qual é o fornecedor. Ver ADR 0007.
- Agenda plugável via `CALENDAR_PROVIDER`: Google Calendar (service account), Microsoft 365/Outlook (Graph),
  CalDAV (iCloud, Nextcloud…) e `fake`.
- WhatsApp Cloud API oficial da Meta (não usar APIs não oficiais como Evolution ou Baileys)
- Persistência: protocolos de repositório com implementação em memória (dev/testes) e **Firestore** (produção)
- Deploy: Google Cloud Run (container) + Cloud Scheduler (jobs)
- Qualidade: pytest, ruff, mypy (strict)

Idioma: código, nomes e docstrings em inglês; mensagens ao paciente, skills e documentação em PT-BR.

## Princípios anti-alucinação (inegociáveis)
- **Horários vêm só do código.** `find_available_slots` calcula horários de forma determinística (agenda real +
  regras). Os horários oferecidos ficam salvos na conversa com ids curtos (`H1`, `H2`…) e **as tools de escrita só
  aceitam esses ids**. O LLM não tem como expressar um horário que não recebeu.
- **Confirmação em duas etapas, garantida no código.** `propose_*` grava uma `pending_action` e devolve um resumo
  canônico (data, hora, procedimento) montado pelo código. `confirm_pending_action` só funciona se a proposta é de
  um turno anterior. Um validador de saída exige que a mensagem de proposta contenha o resumo canônico.
- **Toda escrita é revalidada no backend** contra a agenda real no momento da escrita. Se o horário não estiver mais
  livre, a operação falha e o agente oferece novas opções.
- **Informações da clínica vêm só de `config/clinic.yaml`** via `get_clinic_info`. Fora disso → handoff.
- **Nada clínico.** Um *guard* determinístico roda **antes** do LLM (dor, sangramento, inchaço, trauma, remédio…)
  e faz handoff imediato com acolhimento e, se for urgência, orientação de emergência. O prompt é a segunda camada.
- **Saída estruturada.** O agente devolve `AgentReply` (mensagem + ação + flag de handoff), nunca texto livre.
- **Skills e capabilities não concedem permissão.** As garantias vivem em tools, policies e validadores.

## Arquitetura
```
WhatsApp → POST /webhook → assinatura → parse → Orchestrator
Orchestrator (determinístico, por mensagem):
  dedupe → carrega paciente/conversa
  → comandos determinísticos (#bot, #ok N, #nao N da dentista; botões de lembrete; SAIR)
  → estado: human → silêncio | waiting_approval → aviso
  → guard clínico → handoff (sem LLM)
  → agente PydanticAI (toolsets das capabilities + catálogo de skills)
        cada tool passa pelo PolicyEnforcer: Allow | Deny | RequireApproval
  → envia resposta → salva estado → publica DomainEvents → capabilities.on_event

Cloud Scheduler → POST /jobs/{name} (reminders, recall, followup, cleanup) com bearer JOBS_TOKEN
```

```
app/
  main.py            # FastAPI app factory
  config.py          # Settings (env) — seleção de provedores e segredos
  container.py       # composition root; importa SDKs de forma preguiçosa
  domain/            # modelos Pydantic, ClinicRules (+ loader YAML), eventos de domínio
  scheduling/        # motor de horários PURO (sem I/O, sempre com fuso)
  services/          # casos de uso: booking (revalida na escrita), mensagens canônicas
  harness/           # orchestrator, capability, policies, approvals, events, guard, skills
  agent/             # factory do agente, model.py (resolve modelo), prompt, AgentReply, validadores
  capabilities/      # scheduling, clinic_info, handoff (núcleo) + waitlist, recall, followup (opcionais)
  adapters/
    calendar/        # base.py (Protocol), fake, google, microsoft, caldav
    messaging/       # base.py (Protocol), fake, whatsapp_cloud (+ parsing/assinatura do webhook)
    storage/         # base.py (Protocols), memory, firestore
  integrations/prescriptions/  # só o Protocol (ver ADR 0006)
  cli.py             # `clinic chat`, `clinic eval`, `clinic ms-login`
skills/              # skills PT-BR (SKILL.md com frontmatter), carregadas sob demanda
config/              # clinic.yaml (regras), templates.yaml (templates do WhatsApp)
evals/               # cenários de conversa para medir alucinação com modelo real
docs/adr/            # decisões de arquitetura
deploy/              # instruções de deploy (nada é criado automaticamente)
tests/
```

Regras de dependência: `scheduling/` e `domain/` não importam nada de `adapters/`, `agent/` ou SDKs externos.
Adapters são `typing.Protocol`; o núcleo nunca importa SDKs externos diretamente (só `container.py`, de forma lazy).

## Onde os dados ficam
- **Agenda da dentista** (Google/Outlook/iCloud): fonte da verdade de ocupação. Todo evento bloqueia horário.
- **Firestore**: pacientes, consultas, conversas (estado + últimas N mensagens), aprovações, lista de espera,
  ids processados (dedupe) e auditoria sem PII. TTL nativo para retenção (LGPD).
- **Git**: `config/`, `skills/`. **Segredos**: env vars / Secret Manager.

## Human-in-the-loop
1. **Handoff (conversa):** estado `human`; o bot silencia; a dentista recebe o template `handoff_alert` e libera
   com `#bot <telefone>`.
2. **Aprovação (ação):** uma policy transforma a escrita em `ApprovalRequest`. A dentista responde `#ok N` /
   `#nao N` (ou botões). Na aprovação, a ação é revalidada e executada pelo código e o paciente é avisado com
   mensagem canônica — sem nova chamada ao LLM. Ver ADR 0004.

## Detalhes técnicos importantes
- **Webhook da Meta:** responder ao GET de verificação (`hub.mode`, `hub.verify_token`, `hub.challenge`) e validar
  `X-Hub-Signature-256` com o app secret em todo POST.
- **Idempotência:** a Meta reenvia eventos. Deduplicar por `message_id` antes de processar.
- **Cloud Run com cobrança por requisição:** não use BackgroundTasks para processamento pesado. Processe dentro da
  requisição e responda 200 no final (a idempotência cobre os reenvios).
- **Janela de 24h:** mensagens livres só dentro da janela. Fora dela, apenas templates aprovados.
- **Fuso horário:** `America/Recife` em toda a lógica de agenda. Nunca use datetime sem fuso (ruff `DTZ` bloqueia).
- **LGPD:** guardar o mínimo (nome, telefone, histórico recente, consultas). Sem dados clínicos. Retenção do
  histórico configurável (`retention` em `clinic.yaml`) e aplicada por `/jobs/cleanup` + TTL do Firestore.
  **Modelos gratuitos (ex.: OpenRouter `:free`) só com dados fictícios** — podem usar prompts para treino.
- **Segredos** só por env var ou Secret Manager. Nunca commitar.

## Comandos
```bash
uv sync --all-extras                 # instala tudo (dev incluso)
uv run pytest                        # testes (integration ficam de fora por padrão)
uv run pytest -m integration         # testes contra serviços reais (precisa de credenciais no env)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run clinic chat                   # conversa no terminal com fakes (precisa de AGENT_MODEL + chave)
uv run clinic chat --model test      # roteiro determinístico, sem LLM
uv run clinic eval --model openrouter:<modelo>   # mede a taxa de acerto dos cenários
uv run uvicorn app.main:create_app --factory --reload
```

## Como trabalhar neste repo
- Antes de dar algo por concluído: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
- Toda regra de negócio nova vem acompanhada de teste.
- Não adicionar dependência sem necessidade clara; SDKs de provedores vão em *extras* opcionais.
- Perguntar antes de criar recursos de nuvem ou qualquer coisa que gere custo.
- Commits em Conventional Commits (ver `CONTRIBUTING.md`).
- Decisão de arquitetura nova → ADR em `docs/adr/` e atualização deste arquivo.
- Regras específicas por pasta ficam em `.claude/rules/`.

### Checklists de extensão
- **Novo provedor de agenda:** implementar `CalendarProvider` em `app/adapters/calendar/<nome>.py`, registrar em
  `container.py`, rodar a suíte de contrato (`tests/adapters/calendar/contract.py`), documentar em `deploy/README.md`.
- **Nova capability:** subclasse de `BaseCapability` em `app/capabilities/`, registrar no `CAPABILITY_REGISTRY`,
  configurar em `clinic.yaml > capabilities`, testes de tools/policies/eventos.
- **Nova skill:** `skills/<nome>/SKILL.md` com frontmatter (`name`, `description`, `tools`). As tools citadas
  precisam existir nas capabilities ativas (validação no startup).
