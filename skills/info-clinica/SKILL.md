---
name: info-clinica
description: Responder dúvidas administrativas (endereço, horário, preços, convênios, pagamento, preparo).
tools: [get_clinic_info, request_handoff]
---
1. Escolha o tópico certo e chame `get_clinic_info`. Para preço ou preparo de um procedimento, passe o
   `procedure_id`.
2. Responda só com o que a tool devolveu, sem completar com conhecimento geral.
3. Se o preço for "sob avaliação", explique que o valor é definido pela doutora após a avaliação e ofereça marcar.
4. Se a tool disser que a informação não está cadastrada, diga que vai confirmar com a doutora e chame
   `request_handoff`.
