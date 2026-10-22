---
name: remarcar
description: Trocar o dia/horário de uma consulta já marcada.
tools: [list_my_appointments, find_available_slots, propose_reschedule, confirm_pending_action, cancel_pending_action]
---
1. Chame `list_my_appointments`. Se houver mais de uma consulta, pergunte qual delas remarcar.
2. Busque novos horários com `find_available_slots` usando o MESMO procedimento da consulta.
3. Com o código da consulta e o código do novo horário, chame `propose_reschedule`.
4. Mostre o resumo exato devolvido pela tool e pergunte se pode confirmar.
5. Só após um "sim" em nova mensagem, chame `confirm_pending_action`.
6. Lembre a política de cancelamento/remarcação apenas se a tool mencionar a antecedência.
