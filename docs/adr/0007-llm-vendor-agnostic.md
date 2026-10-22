# 0007 — LLM agnóstico de fornecedor via PydanticAI

- **Status:** aceita
- **Data:** 2026-10-22

## Contexto
Não queremos ficar presos a um fornecedor de LLM. Os testes iniciais usarão OpenRouter com modelos gratuitos; em
produção, o modelo será escolhido por custo × taxa de acerto nos evals.

## Decisão
- PydanticAI é o único wrapper. Só `app/agent/model.py` resolve o modelo.
- `AGENT_MODEL` usa o formato do PydanticAI `provedor:modelo` (ex.: `openrouter:<modelo>`,
  `anthropic:<modelo>`, `google-gla:<modelo>`, `openai:<modelo>`). As chaves usam as variáveis padrão de cada
  provedor (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`).
- `AGENT_FALLBACK_MODELS` (lista separada por vírgula) monta um `FallbackModel` — útil para modelos gratuitos, que
  falham ou batem limite com frequência.
- `AGENT_OUTPUT_MODE` (`tool|native|prompted`) troca a estratégia de saída estruturada para modelos com suporte
  fraco a tool-calling.
- `temperature=0` por padrão; `AGENT_MAX_TOKENS` limita custo.
- `clinic eval --model ...` compara modelos com os mesmos cenários.

## Consequências
- Trocar de fornecedor = trocar variável de ambiente.
- Modelos gratuitos podem usar prompts para treinamento: **só com dados fictícios**. Com pacientes reais, usar
  modelo pago com política de não-treino (requisito de LGPD).
