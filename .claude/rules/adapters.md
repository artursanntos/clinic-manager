---
paths:
  - "app/adapters/**"
  - "app/container.py"
  - "tests/adapters/**"
---
# Adapters (agenda, mensageria, armazenamento)

- Cada tipo tem `base.py` com um `typing.Protocol` e um `fake.py`/`memory.py` em memória usado nos testes e no CLI.
- SDKs externos são importados **só dentro do módulo do adapter**, e o `container.py` importa o adapter de forma
  preguiçosa. O núcleo nunca importa `google`, `caldav`, `httpx` de provedores etc.
- Tipos de SDK não vazam: entram e saem modelos de `app/domain`.
- HTTP com `httpx.AsyncClient`. Testes unitários usam `httpx.MockTransport` (sem dependência extra).
- Agenda: todo provedor passa na suíte de contrato `tests/adapters/calendar/contract.py`. Testes contra o serviço
  real ficam marcados com `@pytest.mark.integration` e são pulados sem credenciais.
- Eventos criados pelo bot carregam o `appointment_id` em metadado privado do provedor e o mínimo de dados do
  paciente (nome + procedimento; nunca telefone).
- Erros de provedor viram exceções de domínio (`CalendarError`, `MessagingError`, `StorageError`).
