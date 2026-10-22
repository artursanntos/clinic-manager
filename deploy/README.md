# Deploy (Google Cloud Run + Firestore + Cloud Scheduler)

> **Nada aqui é executado automaticamente.** Os comandos abaixo criam recursos na nuvem; rode-os você mesmo,
> depois de conferir os limites gratuitos atuais nas páginas de preço do Google Cloud e da Meta.

Variáveis usadas nos exemplos:
```bash
export PROJECT=meu-projeto REGION=southamerica-east1 SERVICE=clinic-manager
export RUN_SA=clinic-run@$PROJECT.iam.gserviceaccount.com
```

## 1. Projeto e APIs
```bash
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com firestore.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com calendar-json.googleapis.com
```

## 2. Service account do serviço
Ela roda o Cloud Run e, no caso do Google Agenda, é com ela que a dentista compartilha a agenda.
```bash
gcloud iam service-accounts create clinic-run --display-name "clinic-manager runtime"
gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$RUN_SA --role roles/datastore.user
gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$RUN_SA --role roles/secretmanager.secretAccessor
```

## 3. Firestore (modo nativo) + retenção (LGPD)
```bash
gcloud firestore databases create --location=$REGION --type=firestore-native
# TTL: documentos são apagados automaticamente após expires_at
gcloud firestore fields ttls update expires_at --collection-group=conversations --enable-ttl
gcloud firestore fields ttls update expires_at --collection-group=processed_messages --enable-ttl
```
O `/jobs/cleanup` (incluído no `daily`) também apaga conversas antigas, como segunda camada.

## 4. Agenda da dentista (escolha um provedor)

### Google Agenda (`CALENDAR_PROVIDER=google`)
1. No Google Agenda dela: Configurações → a agenda → **Compartilhar com pessoas específicas** → adicionar
   `clinic-run@<projeto>.iam.gserviceaccount.com` com **"Fazer alterações nos eventos"**.
2. Em "Integrar agenda", copie o **ID da agenda** → `GOOGLE_CALENDAR_ID` (para a agenda principal é o próprio e-mail).
3. No Cloud Run não há chave: o serviço usa a própria service account (deixe `GOOGLE_CREDENTIALS_FILE` vazio).
   Imagem com `--build-arg EXTRAS="google firestore"` (padrão).

### Outlook / Microsoft 365 (`CALENDAR_PROVIDER=microsoft`)
- **Conta pessoal (outlook.com/hotmail):** registre um app em <https://entra.microsoft.com> → App registrations →
  "Accounts in any organizational directory and personal Microsoft accounts"; em *Authentication* habilite
  **Allow public client flows**; em *API permissions* adicione (delegated) `Calendars.ReadWrite` e `offline_access`.
  Depois, localmente: `MS_CLIENT_ID=<id> uv run clinic ms-login`, faça login com a conta dela e guarde o
  `MS_REFRESH_TOKEN` como segredo. `MS_AUTH_MODE=refresh_token`, `MS_TENANT_ID=consumers`.
  Se o token expirar (inatividade longa ou troca de senha), rode `ms-login` de novo.
- **Microsoft 365 corporativo:** permissão *Application* `Calendars.ReadWrite` com admin consent, um client
  secret, `MS_AUTH_MODE=client_credentials`, `MS_TENANT_ID=<tenant>`, `MS_USER_ID=<e-mail da caixa>`.
- Imagem com `--build-arg EXTRAS="firestore"` (Graph usa só httpx).

### iCloud / CalDAV (`CALENDAR_PROVIDER=caldav`)
1. Em <https://account.apple.com> → Início de sessão e segurança → **Senhas específicas de app** → gerar.
2. `CALDAV_URL=https://caldav.icloud.com`, `CALDAV_USERNAME=<Apple ID>`, `CALDAV_PASSWORD=<senha de app>`,
   `CALDAV_CALENDAR_NAME=<nome exato da agenda>` (vazio = a primeira).
3. Imagem com `--build-arg EXTRAS="caldav firestore"`. Funciona também com Nextcloud, Fastmail, Zimbra etc.

Teste o provedor antes de subir: `uv run pytest -m integration -k <google|caldav>` com as variáveis no ambiente
(cria e apaga um evento de teste daqui a ~400 dias).

## 5. WhatsApp Cloud API (Meta)
1. Crie um app em <https://developers.facebook.com> (tipo Business) e adicione o produto **WhatsApp**.
2. Registre o número do consultório. Atenção: um número na Cloud API normalmente não pode ser usado ao mesmo tempo
   no app do WhatsApp; verifique se a **coexistência** (app WhatsApp Business + Cloud API no mesmo número) está
   disponível para a conta dela, ou use um número dedicado ao assistente.
3. Crie um **System User** no Business Manager e gere um token permanente com `whatsapp_business_messaging` →
   `WHATSAPP_ACCESS_TOKEN`. Copie o **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID` e o **App secret** →
   `WHATSAPP_APP_SECRET`. Invente um `WHATSAPP_VERIFY_TOKEN`.
4. Crie os templates de `config/templates.yaml` no WhatsApp Manager (mesmo nome, pt_BR, categoria e botões) e
   aguarde a aprovação. Templates de marketing (recall, pós-consulta) custam mais que os de utilidade.
5. Depois do deploy: Webhook URL `https://<url-do-cloud-run>/webhook`, verify token igual ao configurado,
   e assine o campo **messages**.

## 6. Segredos
```bash
for name in WHATSAPP_ACCESS_TOKEN WHATSAPP_APP_SECRET WHATSAPP_VERIFY_TOKEN JOBS_TOKEN OPENROUTER_API_KEY; do
  printf '%s' "<valor>" | gcloud secrets create $name --data-file=-   # troque <valor> para cada um
done
# conforme o provedor: MS_REFRESH_TOKEN, CALDAV_PASSWORD, ANTHROPIC_API_KEY...
```

## 7. Deploy
```bash
gcloud run deploy $SERVICE --source . --region $REGION --service-account $RUN_SA \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --concurrency 10 --memory 512Mi --cpu 1 \
  --set-env-vars "MESSAGING_PROVIDER=whatsapp_cloud,STORAGE_BACKEND=firestore,FIRESTORE_PROJECT=$PROJECT,\
CALENDAR_PROVIDER=google,GOOGLE_CALENDAR_ID=<id-da-agenda>,AGENT_MODEL=<provedor:modelo>,WHATSAPP_PHONE_NUMBER_ID=<id>" \
  --set-secrets "WHATSAPP_ACCESS_TOKEN=WHATSAPP_ACCESS_TOKEN:latest,WHATSAPP_APP_SECRET=WHATSAPP_APP_SECRET:latest,\
WHATSAPP_VERIFY_TOKEN=WHATSAPP_VERIFY_TOKEN:latest,JOBS_TOKEN=JOBS_TOKEN:latest,OPENROUTER_API_KEY=OPENROUTER_API_KEY:latest"
```
- `--allow-unauthenticated` é necessário porque a Meta chama o webhook; a assinatura `X-Hub-Signature-256` protege
  o POST e o `/jobs/*` exige `Authorization: Bearer $JOBS_TOKEN`.
- `--max-instances 1`: o lock por paciente é em memória; para uma única dentista uma instância basta.
- Cobrança por requisição (padrão): o processamento acontece dentro da requisição do webhook (ver `CLAUDE.md`).
- `--source .` usa o Cloud Build com o `Dockerfile`. Para outro provedor de agenda, faça o build com o `EXTRAS`
  certo (`gcloud builds submit --tag ...` com `--build-arg`) e use `--image`.

## 8. Cloud Scheduler (um job só, dentro do free tier)
O job `daily` roda lembretes (consultas de amanhã), recall, pós-consulta e limpeza.
```bash
URL=$(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')
gcloud scheduler jobs create http clinic-daily --location $REGION \
  --schedule "0 10 * * *" --time-zone "America/Recife" \
  --uri "$URL/jobs/daily" --http-method POST \
  --headers "Authorization=Bearer <JOBS_TOKEN>"
```

## 9. Antes de atender pacientes reais
- [ ] `config/clinic.yaml` sem nenhum `EXEMPLO` (horários, preços, endereço, telefone da dentista, feriados).
- [ ] Modelo **pago** com política de não-uso de dados para treino (nada de modelos `:free` com pacientes reais).
- [ ] `uv run clinic eval --model <modelo-de-produção>` com 100% dos cenários.
- [ ] Templates aprovados na Meta e testados com um número de teste (chame `/jobs/daily` com uma consulta fictícia
      marcada para amanhã nesse número).
- [ ] Aviso de privacidade (LGPD) combinado com a dentista: o que é guardado, por quanto tempo, e como pedir exclusão.
- [ ] Teste o handoff e a aprovação ponta a ponta com o WhatsApp da dentista (`#ok N`, `#bot <telefone>`).
