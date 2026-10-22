# 0001 — Provedores externos atrás de Protocols, escolhidos por configuração

- **Status:** aceita
- **Data:** 2026-10-22

## Contexto
A dentista pode usar Google Agenda, Outlook ou o calendário do iPhone (iCloud). Também queremos trocar
armazenamento (memória ↔ Firestore) e mensageria (fake ↔ WhatsApp Cloud API) sem tocar no núcleo, e testar tudo
sem rede.

## Decisão
- Cada integração tem um `typing.Protocol` em `app/adapters/<tipo>/base.py` e um fake em memória.
- `CALENDAR_PROVIDER` (`fake|google|microsoft|caldav`), `STORAGE_BACKEND` (`memory|firestore`) e
  `MESSAGING_PROVIDER` (`fake|whatsapp_cloud`) escolhem a implementação em `app/container.py`, que importa o
  adapter de forma preguiçosa. SDKs ficam em *extras* opcionais do `pyproject.toml`.
- A agenda é a fonte da verdade de ocupação: **todo** evento bloqueia horário (inclusive pessoais e recorrentes).
- Google e Microsoft usam REST via `httpx` (Google com `google-auth` só para token), evitando SDKs pesados.
  CalDAV usa a lib `caldav` (síncrona) dentro de `anyio.to_thread`.
- Uma suíte de contrato única roda contra o fake sempre e contra provedores reais quando há credenciais.

## Consequências
- Adicionar um provedor = um arquivo + registro + contrato verde.
- O fake precisa refletir fielmente o contrato (por isso a suíte compartilhada).
- Microsoft pessoal (Outlook.com) exige fluxo delegado com refresh token (`clinic ms-login`); contas M365 usam
  client credentials.

## Alternativas consideradas
- `google-api-python-client`: pesado e síncrono; REST direto é suficiente.
- Agregadores pagos de calendário (Nylas, Cronofy): custo mensal incompatível com o objetivo de free tier.
