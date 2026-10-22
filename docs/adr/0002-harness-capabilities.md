# 0002 — Harness determinístico com capabilities plugáveis

- **Status:** aceita
- **Data:** 2026-10-22

## Contexto
Queremos expandir o assistente (lista de espera, recall, follow-up, futuras integrações) sem reescrever o núcleo e
sem enfraquecer as garantias anti-alucinação.

## Decisão
- Um **Orchestrator** determinístico processa cada mensagem: dedupe → comandos → estado → guard clínico →
  agente → envio → persistência → eventos. O LLM participa de um único passo.
- **Capability** (`app/harness/capability.py`) é a unidade de extensão: `instructions()`, `toolset()`
  (toolset do PydanticAI), `policies()`, `on_event()` e `jobs()`. O próprio agendamento é uma capability.
- Capabilities são registradas em `CAPABILITY_REGISTRY` e habilitadas em `clinic.yaml > capabilities`.
- Um `PolicyEnforcer` (capability do PydanticAI, hook `before_tool_execute`) aplica as policies de todas as
  capabilities a toda chamada de tool: `Allow`, `Deny(motivo)` ou `RequireApproval`.
- Eventos de domínio (`AppointmentBooked`, `AppointmentCancelled`, …) passam por um event bus em processo; falhas
  de handlers são logadas e nunca quebram o fluxo do paciente.
- Jobs das capabilities são expostos como `POST /jobs/{nome}`.

## Consequências
- Nova funcionalidade = nova capability, testável isoladamente.
- Ordem de capabilities importa para instruções e policies; é a ordem do `clinic.yaml`.

## Alternativas consideradas
- **Multi-agente** (roteador + especialistas): mais custo por mensagem e menos previsibilidade para uma única
  dentista. Reavaliar se o escopo crescer muito.
- **Carregar MCP/código externo em runtime**: superfície de ataque e de alucinação maior. Uma capability pode
  encapsular um servidor MCP no futuro, sempre atrás das mesmas policies.
