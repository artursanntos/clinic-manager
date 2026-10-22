# 0004 — Human-in-the-loop: handoff de conversa e aprovação de ação

- **Status:** aceita
- **Data:** 2026-10-22

## Contexto
Algumas situações exigem a dentista: dúvidas clínicas, perguntas fora do escopo e decisões de agenda que só ela pode
tomar (ex.: encaixes fora da grade normal).

## Decisão
Dois níveis:
1. **Handoff:** a conversa vai para o estado `human`; o bot silencia; a dentista recebe o template `handoff_alert` e
   libera com `#bot <telefone>`.
2. **Aprovação de ação:** uma policy devolve `RequireApproval` para uma escrita (ex.: confirmar um horário de
   encaixe). Criamos uma `ApprovalRequest` com a ação de domínio serializada, avisamos a dentista
   (`approval_request`, com `#ok N` / `#nao N`) e respondemos ao paciente que estamos confirmando com a doutora.
   Na decisão, **o código** revalida e executa a ação e avisa o paciente com uma mensagem canônica.

Não usamos o mecanismo de *deferred tools* do PydanticAI para retomar a execução do agente: a aprovação pode levar
horas, o paciente pode ter mandado outras mensagens, e retomar a execução custaria outra chamada ao LLM. Executar a
ação de domínio diretamente é mais barato, previsível e auditável.

## Consequências
- Aprovação não depende do LLM; o texto ao paciente é determinístico.
- Policies configuráveis em `clinic.yaml > approvals`.
- Se a ação aprovada não for mais válida (horário ocupado), o paciente é avisado e convidado a escolher outro.

## Alternativas consideradas
- `DeferredToolRequests` + retomada da execução do agente: elegante para aprovações síncronas, inadequado para
  aprovações assíncronas via WhatsApp.
