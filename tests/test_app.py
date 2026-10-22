"""FastAPI endpoints end to end (webhook → orchestrator → fake WhatsApp outbox)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from tests.conftest import PATIENT, HarnessFactory
from tests.helpers import ScriptModel

SECRET = "app-secret-test"


def signed(body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def wa_text(message_id: str, text: str, sender: str = PATIENT) -> dict[str, Any]:
    msg = {"from": sender, "id": message_id, "timestamp": "1761500000", "type": "text", "text": {"body": text}}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


@pytest.fixture
def client_and_harness(make_harness: HarnessFactory) -> Any:
    script = ScriptModel("Olá! Como posso ajudar?")
    h = make_harness(script.model)
    settings = h.container.settings.model_copy(
        update={
            "whatsapp_app_secret": SecretStr(SECRET),
            "whatsapp_verify_token": SecretStr("verify-me"),
            "jobs_token": SecretStr("jobs-token"),
        }
    )
    app = create_app(h.container, settings)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return client, h, script


async def test_health(client_and_harness: Any) -> None:
    client, _, _ = client_and_harness
    assert (await client.get("/health")).json() == {"status": "ok"}


async def test_webhook_verification(client_and_harness: Any) -> None:
    client, _, _ = client_and_harness
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"}
    response = await client.get("/webhook", params=params)
    assert response.status_code == 200 and response.text == "42"
    bad = await client.get("/webhook", params={**params, "hub.verify_token": "nope"})
    assert bad.status_code == 403


async def test_signed_message_is_answered_once(client_and_harness: Any) -> None:
    client, h, script = client_and_harness
    raw, headers = signed(wa_text("wamid.1", "oi"))
    response = await client.post("/webhook", content=raw, headers=headers)
    assert response.status_code == 200 and response.json()["handled"] == 1
    assert [m.text for m in h.messaging.to(PATIENT)] == ["Olá! Como posso ajudar?"]
    # Meta retries the same event → no second reply, no second LLM call
    await client.post("/webhook", content=raw, headers=headers)
    assert len(h.messaging.to(PATIENT)) == 1 and script.calls == 1


async def test_bad_signature_is_rejected(client_and_harness: Any) -> None:
    client, h, script = client_and_harness
    raw, headers = signed(wa_text("wamid.2", "oi"))
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    assert (await client.post("/webhook", content=raw, headers=headers)).status_code == 403
    assert h.messaging.outbox == [] and script.calls == 0


async def test_jobs_require_token(client_and_harness: Any) -> None:
    client, _, _ = client_and_harness
    assert (await client.post("/jobs/reminders")).status_code == 401
    assert (await client.post("/jobs/reminders", headers={"Authorization": "Bearer wrong"})).status_code == 401
    ok = await client.post("/jobs/reminders", headers={"Authorization": "Bearer jobs-token"})
    assert ok.status_code == 200 and ok.json()["name"] == "reminders"
    missing = await client.post("/jobs/nope", headers={"Authorization": "Bearer jobs-token"})
    assert missing.status_code == 404


async def test_brazilian_ninth_digit_dentist_detection(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model)
    # dentist configured as 5581900000001 (13 digits); WhatsApp may deliver 558100000001 (no extra 9)
    replies = await h.send("#pendentes", phone="558100000001")
    assert replies == ["Nenhum pedido aguardando aprovação."]
