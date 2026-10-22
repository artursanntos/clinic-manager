---
name: agendar
description: Marcar uma nova consulta (do pedido do paciente até a confirmação).
tools: [find_available_slots, propose_booking, confirm_pending_action, cancel_pending_action]
---
1. Descubra o procedimento. Se o paciente não souber, sugira a avaliação (id `avaliacao`).
2. Pergunte se prefere manhã ou tarde e algum dia da semana, se ele ainda não disse. Não faça mais de uma
   pergunta por mensagem.
3. Chame `find_available_slots` e apresente as opções exatamente como vieram, com os códigos (H1, H2…).
   Se nada servir, ofereça outro período, outra semana ou a lista de espera.
4. Quando o paciente escolher, garanta que você tem o nome completo dele. Chame `propose_booking`.
5. Mostre o resumo exatamente como a tool devolveu e pergunte: "Posso confirmar?".
6. Só com um "sim" do paciente em uma nova mensagem, chame `confirm_pending_action` e repita a mensagem de
   confirmação devolvida. Se ele disser não, chame `cancel_pending_action` e ofereça outras opções.
7. Horários marcados como "encaixe" dependem da doutora: deixe isso claro antes de propor.
