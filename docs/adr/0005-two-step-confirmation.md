# 0005 — Confirmação em duas etapas e horários por id

- **Status:** aceita
- **Data:** 2026-10-22

## Contexto
O risco número um é o bot "confirmar" um horário que não existe ou que o paciente não aceitou explicitamente.

## Decisão
- `find_available_slots` salva os horários oferecidos na conversa com ids curtos (`H1`, `H2`…). As tools de escrita
  aceitam **apenas** esses ids.
- `propose_booking` / `propose_reschedule` / `propose_cancel` gravam uma `pending_action` com o número do turno e
  devolvem um resumo canônico gerado pelo código.
- `confirm_pending_action` só executa se a proposta foi criada num turno **anterior** (o paciente teve a chance de
  ler e responder) e revalida o horário na agenda.
- Um validador de saída exige que, quando o agente propõe algo, a mensagem contenha o resumo canônico.

## Consequências
- O LLM não consegue expressar um horário que o código não gerou.
- Uma confirmação exige pelo menos duas mensagens do paciente; é o comportamento desejado.

## Alternativas consideradas
- Confiar no prompt ("sempre confirme antes"): insuficiente para o objetivo de zero alucinação.
