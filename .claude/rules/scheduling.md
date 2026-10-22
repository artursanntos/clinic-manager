---
paths:
  - "app/scheduling/**"
  - "app/domain/**"
  - "tests/scheduling/**"
---
# Motor de horários e domínio

- `app/scheduling/` é **puro**: sem I/O, sem rede, sem `datetime.now()`. O "agora" entra como parâmetro (`now`).
- Todo `datetime` tem fuso. Use `ZoneInfo(rules.timezone)` (padrão `America/Recife`). Ruff `DTZ` reprova naive.
- Não importe nada de `app/adapters`, `app/agent`, `app/harness` ou SDKs externos aqui.
- Toda regra nova de agenda vem com teste de borda: almoço, fim do expediente, procedimento que não cabe,
  feriado, horário passado, encaixe entre consultas, antecedência mínima/máxima, janelas de encaixe.
- Ordenação de saída é determinística (por início, depois fim). Mesmo input → mesmo output.
- Valores de exemplo em `config/clinic.yaml` ficam marcados como `EXEMPLO` até a dentista fornecer os reais.
