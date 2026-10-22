# 0003 — Skills em markdown com carregamento sob demanda

- **Status:** aceita
- **Data:** 2026-10-22

## Contexto
O comportamento de conversa (tom, ordem das perguntas, como lidar com cada fluxo) muda com mais frequência que o
código e deveria ser ajustável sem deploy de lógica. Modelos baratos sofrem com prompts longos.

## Decisão
- Skills ficam em `skills/<nome>/SKILL.md` (PT-BR), com frontmatter `name`, `description` e `tools`.
- Só o catálogo (nome + descrição) entra no prompt; o agente chama `load_skill(nome)` para ler o conteúdo.
- No startup, toda tool citada por uma skill precisa existir nas capabilities ativas — senão o app não sobe.
- Skills **não concedem permissão**: invariantes vivem em tools, policies e validadores.

## Consequências
- Ajustar conversa = editar markdown + rodar evals.
- Uma skill mal escrita pode piorar a conversa, mas não consegue marcar horário inventado nem dar informação
  clínica.

## Alternativas consideradas
- Prompt monolítico: mais tokens por mensagem e mais difícil de manter.
- Skills geradas/alteradas pelo próprio LLM: risco alto, sem revisão humana.
