# Contribuindo

Guia curto para quem (pessoa ou agente) for mexer neste repositório. As regras de arquitetura estão em
[`CLAUDE.md`](CLAUDE.md) e as decisões em [`docs/adr/`](docs/adr/).

## Setup
```bash
# Python 3.12 e uv (https://docs.astral.sh/uv/)
uv sync --all-extras          # dependências + extras de todos os provedores + ferramentas de dev
cp .env.example .env          # preencha só o que for usar; nunca commite o .env
uvx pre-commit install        # opcional: roda ruff/mypy antes de cada commit
```

Rodar localmente sem nenhuma conta externa:
```bash
uv run clinic chat --model test                     # roteiro determinístico, sem LLM
AGENT_MODEL=openrouter:<modelo> OPENROUTER_API_KEY=... uv run clinic chat   # LLM real, agenda fake
```

## Fluxo de trabalho
1. Crie uma branch a partir de `main`: `feat/<assunto>`, `fix/<assunto>`, `docs/<assunto>`, `chore/<assunto>`.
2. Faça mudanças pequenas e coesas. Regra de negócio nova → teste novo.
3. Antes de abrir o PR, tudo verde:
   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
   ```
4. Abra o PR usando o template. Mudou uma decisão de arquitetura? Inclua um ADR.

## Commits — Conventional Commits
Formato: `<tipo>(<escopo>): <resumo no imperativo, em inglês ou português, até ~72 caracteres>`

Tipos: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`, `perf`, `build`.

Escopos:

| Escopo | Onde |
|---|---|
| `scheduling` | `app/scheduling/`, regras de horário |
| `domain` | `app/domain/` |
| `agent` | `app/agent/`, prompt, validadores |
| `harness` | `app/harness/` (orchestrator, policies, aprovações, guard, skills loader) |
| `capabilities` | `app/capabilities/` |
| `skills` | `skills/` |
| `calendar` / `messaging` / `storage` | `app/adapters/<tipo>/` |
| `jobs` | endpoints e lógica de `/jobs/*` |
| `config` | `config/*.yaml` |
| `infra` | Dockerfile, `deploy/`, CI |
| `docs` | documentação e ADRs |

Exemplos:
```
feat(scheduling): support encaixe windows that require approval
fix(calendar): expand recurring events in CalDAV busy query
docs(adr): record vendor-agnostic LLM decision
```
Breaking change: `feat(agent)!: ...` + rodapé `BREAKING CHANGE: ...`.

## Definição de pronto
- [ ] `ruff check`, `ruff format --check`, `mypy` e `pytest` verdes.
- [ ] Toda regra de negócio nova tem teste (inclusive bordas).
- [ ] Nenhuma invariante anti-alucinação foi enfraquecida (ver `CLAUDE.md`).
- [ ] Nenhum segredo, telefone real ou dado de paciente em código, fixtures ou logs.
- [ ] Mensagens ao paciente em PT-BR; código em inglês.
- [ ] `CLAUDE.md`/ADR atualizados se a arquitetura mudou.

## Receitas de extensão

### Adicionar um provedor de agenda
1. `app/adapters/calendar/<nome>.py` implementando `CalendarProvider` (`list_busy`, `create_event`,
   `update_event`, `delete_event`). Use `httpx` quando possível; SDK só se inevitável, e como *extra* opcional.
2. Registre o nome em `app/container.py` (`_build_calendar`) com import preguiçoso.
3. Testes unitários com `httpx.MockTransport` + rode a suíte de contrato
   (`tests/adapters/calendar/contract.py`) com um teste `@pytest.mark.integration` para o serviço real.
4. Documente variáveis de ambiente em `.env.example` e o passo a passo de compartilhamento em `deploy/README.md`.

### Adicionar uma capability
1. `app/capabilities/<nome>.py` com uma subclasse de `BaseCapability`. Sobrescreva só o que usar:
   `instructions()`, `toolset()`, `policies()`, `on_event()`, `jobs()`.
2. Registre em `CAPABILITY_REGISTRY` e adicione a configuração em `clinic.yaml > capabilities.<nome>`.
3. Templates novos do WhatsApp em `config/templates.yaml` (e aprovados no painel da Meta).
4. Testes: tools (com fakes), policies, handlers de evento e jobs (idempotência).

### Adicionar uma skill
1. `skills/<nome>/SKILL.md` com frontmatter:
   ```markdown
   ---
   name: nome-da-skill
   description: Uma linha dizendo quando usar.
   tools: [find_available_slots, propose_booking]
   ---
   Passos e tom da conversa, em PT-BR.
   ```
2. Sem preços, horários ou conteúdo clínico — isso vem das tools.
3. Rode `uv run pytest tests/harness/test_skills.py` (valida frontmatter e tools referenciadas).
4. Se a skill muda comportamento importante, acrescente um cenário em `evals/scenarios/`.
