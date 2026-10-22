# Skills

Instruções de conversa em PT-BR, carregadas sob demanda pelo agente (`load_skill`). Ver
[ADR 0003](../docs/adr/0003-skills-progressive-disclosure.md) e `.claude/rules/capabilities-skills.md`.

- Uma pasta por skill, com `SKILL.md` e frontmatter `name` (igual ao nome da pasta), `description` e `tools`.
- Skills **não concedem permissão**: só orientam tom e passos. Horários, preços e informações vêm das tools.
- Toda tool listada em `tools` precisa existir em alguma capability (senão o app não sobe). Skills cujas tools
  pertencem a uma capability desativada ficam fora do catálogo automaticamente.
