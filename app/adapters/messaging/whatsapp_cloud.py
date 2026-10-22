"""Official WhatsApp Cloud API (Meta). Sending via Graph API + webhook parsing and signature verification."""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.messaging.base import InboundMessage, MessagingError, TemplateMessage

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"


class WhatsAppCloudClient:
    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v23.0",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{GRAPH_URL}/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def _post(self, payload: dict[str, Any]) -> str:
        try:
            response = await self._client.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            raise MessagingError(f"whatsapp request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            code = _error_code(response)
            raise MessagingError(f"whatsapp api error: http {response.status_code}, code {code}")
        data = response.json()
        try:
            return str(data["messages"][0]["id"])
        except (KeyError, IndexError, TypeError) as exc:
            raise MessagingError("unexpected whatsapp response shape") from exc

    async def send_text(self, to: str, text: str) -> str:
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
        )

    async def send_template(self, to: str, template: TemplateMessage) -> str:
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": build_template_payload(template),
            }
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_template_payload(template: TemplateMessage) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    if template.body_params:
        components.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in template.body_params]})
    for index, payload in enumerate(template.button_payloads):
        components.append(
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": payload}],
            }
        )
    body: dict[str, Any] = {"name": template.name, "language": {"code": template.language}}
    if components:
        body["components"] = components
    return body


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("code", "?"))
    except ValueError:
        return "?"


# --------------------------------------------------------------------------- webhook


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Validate ``X-Hub-Signature-256: sha256=<hex>`` in constant time."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extract inbound messages; ignores delivery/read statuses. Media messages come with empty text."""
    messages: list[InboundMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", []) or []}
            for raw in value.get("messages", []) or []:
                parsed = _parse_message(raw, names)
                if parsed is not None:
                    messages.append(parsed)
    return messages


def _parse_message(raw: dict[str, Any], names: dict[Any, Any]) -> InboundMessage | None:
    message_id, sender = raw.get("id"), raw.get("from")
    if not message_id or not sender:
        return None
    try:
        timestamp = datetime.fromtimestamp(int(raw.get("timestamp", 0)), tz=UTC)
    except (TypeError, ValueError):
        timestamp = datetime.now(UTC)
    kind = raw.get("type")
    text, payload = "", None
    if kind == "text":
        text = str((raw.get("text") or {}).get("body", ""))
    elif kind == "button":  # quick reply on a template
        button = raw.get("button") or {}
        text, payload = str(button.get("text", "")), button.get("payload")
    elif kind == "interactive":
        interactive = raw.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text, payload = str(reply.get("title", "")), reply.get("id")
    return InboundMessage(
        message_id=str(message_id),
        from_phone=str(sender),
        timestamp=timestamp,
        text=text,
        button_payload=str(payload) if payload else None,
        profile_name=names.get(sender),
    )
