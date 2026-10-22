from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest

from app.adapters.messaging.base import MessagingError, TemplateMessage
from app.adapters.messaging.whatsapp_cloud import WhatsAppCloudClient, parse_webhook, verify_signature


def make_client(handler: Any) -> tuple[WhatsAppCloudClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)  # type: ignore[no-any-return]

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return WhatsAppCloudClient(access_token="tok", phone_number_id="123", client=client), seen


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})


async def test_send_text() -> None:
    client, seen = make_client(ok)
    assert await client.send_text("5581999990000", "Olá") == "wamid.X"
    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/123/messages")
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert body["type"] == "text" and body["text"]["body"] == "Olá" and body["to"] == "5581999990000"


async def test_send_template_with_buttons() -> None:
    client, seen = make_client(ok)
    template = TemplateMessage(
        name="lembrete_consulta", language="pt_BR", body_params=["Ana", "Limpeza"], button_payloads=["CONFIRM:a1"]
    )
    await client.send_template("5581999990000", template)
    body = json.loads(seen[0].content)["template"]
    assert body["name"] == "lembrete_consulta" and body["language"] == {"code": "pt_BR"}
    assert body["components"][0]["parameters"] == [{"type": "text", "text": "Ana"}, {"type": "text", "text": "Limpeza"}]
    assert body["components"][1] == {
        "type": "button",
        "sub_type": "quick_reply",
        "index": "0",
        "parameters": [{"type": "payload", "payload": "CONFIRM:a1"}],
    }


async def test_api_error_becomes_messaging_error_without_pii() -> None:
    client, _ = make_client(lambda r: httpx.Response(400, json={"error": {"code": 131047, "message": "x"}}))
    with pytest.raises(MessagingError) as exc:
        await client.send_text("5581999990000", "Olá")
    assert "131047" in str(exc.value) and "5581999990000" not in str(exc.value)


def test_verify_signature() -> None:
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good)
    assert not verify_signature("secret", body, "sha256=deadbeef")
    assert not verify_signature("secret", body, None)
    assert not verify_signature("other", body, good)


def payload(*messages: dict[str, Any], statuses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": "558199990000", "profile": {"name": "Ana"}}],
        "messages": list(messages),
    }
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": value}]}]}


def test_parse_text_button_interactive_and_media() -> None:
    base = {"from": "558199990000", "timestamp": "1761500000"}
    msgs = parse_webhook(
        payload(
            {**base, "id": "m1", "type": "text", "text": {"body": "oi"}},
            {**base, "id": "m2", "type": "button", "button": {"text": "Confirmar", "payload": "CONFIRM:abc"}},
            {**base, "id": "m3", "type": "interactive", "interactive": {"button_reply": {"id": "X:1", "title": "Ok"}}},
            {**base, "id": "m4", "type": "image", "image": {"id": "media"}},
        )
    )
    assert [m.message_id for m in msgs] == ["m1", "m2", "m3", "m4"]
    assert msgs[0].text == "oi" and msgs[0].profile_name == "Ana" and msgs[0].timestamp.tzinfo is not None
    assert msgs[1].button_payload == "CONFIRM:abc"
    assert msgs[2].button_payload == "X:1"
    assert msgs[3].text == "" and msgs[3].button_payload is None


def test_statuses_are_ignored() -> None:
    assert parse_webhook(payload(statuses=[{"id": "x", "status": "read"}])) == []
    assert parse_webhook({}) == []
