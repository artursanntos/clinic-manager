---
paths:
  - "tests/**"
---
# Testes

- `uv run pytest` precisa passar sem rede e sem credenciais.
- Use os fakes (`FakeCalendar`, `FakeMessaging`, `InMemoryStorage`) e as fixtures de `tests/conftest.py`.
- "Agora" é sempre injetado (`now=`); nunca dependa do relógio real.
- Agente: `FunctionModel` para roteirizar chamadas de tools; asserte efeitos (agenda, outbox, estado) e não só texto.
- Toda regra de negócio nova → teste. Todo bug corrigido → teste de regressão.
- `@pytest.mark.integration` para testes contra serviços reais; eles leem credenciais do env e se pulam sem elas.
