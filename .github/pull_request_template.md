## O que muda
<!-- Resumo em 1–3 linhas. -->

## Por quê
<!-- Problema ou necessidade. Link para issue/ADR, se houver. -->

## Como foi testado
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest`
- [ ] Testes novos cobrem a regra de negócio / bug

## Checklist
- [ ] Nenhuma invariante anti-alucinação foi enfraquecida (`CLAUDE.md`)
- [ ] Sem segredos, telefones reais ou dados de pacientes
- [ ] `CLAUDE.md` / ADR atualizados se a arquitetura mudou
- [ ] Templates novos do WhatsApp listados em `config/templates.yaml`
