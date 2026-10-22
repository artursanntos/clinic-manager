---
paths:
  - "app/**"
  - "deploy/**"
---
# Segurança e LGPD

- Segredos só por env var / Secret Manager. Nunca em código, YAML ou testes (use valores fictícios óbvios).
- Logs sem PII: logue ids (appointment_id, conversation_id) e telefone mascarado (`mask_phone`), nunca nome ou texto
  da mensagem do paciente.
- Guardamos o mínimo: nome, telefone, histórico recente da conversa, consultas, opt-outs. Nada clínico.
- Validação de assinatura do webhook em todo POST, com comparação em tempo constante.
- Endpoints `/jobs/*` exigem `Authorization: Bearer $JOBS_TOKEN`.
- Modelos de LLM gratuitos podem treinar com os prompts: só com dados fictícios. Produção usa modelo pago com
  política de não-treino.
