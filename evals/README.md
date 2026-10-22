# Evals

Cenários de conversa que rodam pelo harness **real** (tools, policies, validadores, orchestrator) com agenda,
armazenamento e WhatsApp falsos, e conferem o resultado.

```bash
uv run clinic eval --model test                                # roteiro determinístico (roda no CI)
OPENROUTER_API_KEY=... uv run clinic eval --model openrouter:<modelo> -v
uv run clinic eval --model anthropic:<modelo> --only cancel
```

Além das expectativas de cada cenário, **todo turno** é checado para:
- agenda alterada sem proposta confirmada de um turno anterior;
- preço (R$) que não existe no `clinic.yaml`;
- horário que não foi oferecido pelo código, não é uma consulta existente nem foi escrito pelo paciente.

Use para escolher o modelo de produção: rode os mesmos cenários com cada candidato e compare a taxa de acerto e o
custo. **Só dados fictícios** — modelos gratuitos podem usar os prompts para treino.

Formato de um cenário: ver `evals/runner.py` (`Scenario`, `Turn`, `Expect`).
