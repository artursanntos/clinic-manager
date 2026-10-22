# 0006 — Receitas: somente entrega, nunca geração (interface apenas)

- **Status:** aceita (nada implementado além da interface)
- **Data:** 2026-10-22

## Contexto
Receitas são conteúdo clínico e documento legal (assinatura digital ICP-Brasil, regras do CFO). O bot não pode
gerar, sugerir, explicar ou alterar receitas.

## Decisão
- Existe só o protocolo `PrescriptionDeliveryProvider` em `app/integrations/prescriptions/base.py`.
- Desenho futuro: a dentista emite e assina a receita numa plataforma própria (ex.: Memed). Um job busca documentos
  prontos e o bot **apenas entrega** o link/arquivo ao paciente via template. O LLM nunca vê o conteúdo.
- Qualquer pergunta do paciente sobre a receita → handoff.
- Nada é registrado no `CAPABILITY_REGISTRY` até a dentista escolher a plataforma.

## Consequências
- O núcleo continua sem nenhum dado clínico.
- A implementação futura será uma capability com um job de entrega e nenhuma tool exposta ao LLM.
