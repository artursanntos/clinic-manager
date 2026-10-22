---
paths:
  - "app/capabilities/**"
  - "app/harness/**"
  - "skills/**"
  - "config/clinic.yaml"
---
# Capabilities e skills

**Capability** = pacote de comportamento: instruções curtas (PT-BR), toolset PydanticAI, policies, handlers de
eventos de domínio e jobs. Até o agendamento é uma capability.
- Herde de `BaseCapability` (`app/harness/capability.py`) e sobrescreva só o necessário.
- Registre em `CAPABILITY_REGISTRY` (`app/capabilities/__init__.py`) e habilite em `clinic.yaml > capabilities`.
- `on_event` nunca pode quebrar o fluxo do paciente: exceções são logadas pelo event bus.
- Jobs são idempotentes (podem rodar duas vezes no mesmo dia sem duplicar mensagens).
- Mensagens fora da janela de 24h usam template de `config/templates.yaml`.

**Skill** = instrução de conversa em markdown (`skills/<nome>/SKILL.md`), carregada sob demanda com `load_skill`.
- Frontmatter obrigatório: `name`, `description`; opcional: `tools` (lista).
- Skills **não concedem permissão** nem mudam regras de negócio; só orientam tom e passos.
- Toda tool citada em `tools` precisa existir nas capabilities ativas (o startup falha se não existir).
- Nunca coloque preço, horário ou informação clínica em skill — isso vem de `clinic.yaml` via tools.
