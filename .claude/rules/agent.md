---
paths:
  - "app/agent/**"
  - "app/harness/**"
  - "app/capabilities/**"
  - "skills/**"
  - "evals/**"
---
# Agente, harness e garantias anti-alucinação

Invariantes que **nenhuma mudança pode enfraquecer** (se precisar, abra um ADR antes):
1. Horários só chegam ao paciente via `find_available_slots`; escrita só aceita ids de `conversation.offered_slots`.
2. Escrita só via `confirm_pending_action`, e só se a `pending_action` foi criada num turno anterior.
3. Toda escrita revalida contra a agenda real (`services/booking.py`).
4. Informação da clínica só de `ClinicRules` (`get_clinic_info`). Desconhecido → handoff.
5. Guard clínico determinístico roda antes do LLM. Nada clínico sai do bot.
6. Saída sempre `AgentReply` estruturado.

Diretrizes:
- O agente não sabe qual fornecedor de LLM está em uso. Só `app/agent/model.py` resolve `AGENT_MODEL`.
- Mensagens ao paciente em PT-BR, curtas, educadas, sem emojis em excesso. Datas por extenso via
  `services/messages.py` (formatação canônica), nunca formatadas pelo LLM.
- Tools devolvem texto/estruturas que o modelo pode repetir literalmente; nada de dados que o modelo precise "calcular".
- Policies (`harness/policies.py`) decidem Allow/Deny/RequireApproval; tools não checam permissão por conta própria
  além das invariantes acima.
- Testes de agente usam `FunctionModel`/`TestModel` — nunca chamam LLM real. Cenários com LLM real vão em `evals/`.
