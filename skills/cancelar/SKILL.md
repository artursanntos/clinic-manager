---
name: cancelar
description: Cancelar uma consulta já marcada.
tools: [list_my_appointments, propose_cancel, confirm_pending_action, cancel_pending_action]
---
1. Chame `list_my_appointments` e identifique a consulta (pergunte se houver mais de uma).
2. Antes de cancelar, ofereça remarcar — muitas vezes o paciente prefere outro horário.
3. Se ele quiser mesmo cancelar, chame `propose_cancel`, mostre o resumo exato e pergunte se pode confirmar.
   Se a tool avisar sobre cancelamento em cima da hora, informe a política com as palavras da tool.
4. Só após um "sim" em nova mensagem, chame `confirm_pending_action`.
5. Feche com cordialidade e diga que ele pode marcar de novo quando quiser.
