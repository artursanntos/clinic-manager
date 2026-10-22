"""Outbound notifications that respect the WhatsApp 24h window."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Literal

from app.adapters.messaging.base import MessagingError
from app.domain.models import Conversation, mask_phone
from app.harness.context import Services

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, services: Services) -> None:
        self.services = services

    async def to_patient(self, phone: str, text: str, *, role: Literal["bot", "dentist"] = "bot") -> None:
        """Free text inside the 24h window, otherwise the generic ``patient_update`` template."""
        s = self.services
        conversation = await s.storage.conversations.get(phone)
        try:
            if conversation is not None and conversation.within_service_window(s.now()):
                await s.messaging.send_text(phone, text)
            else:
                await s.messaging.send_template(phone, s.templates.build("patient_update", message=text))
        except MessagingError:
            log.exception("failed to notify patient %s", mask_phone(phone))
            return
        if conversation is not None:
            conversation.add_message(role, text, s.now())
            await s.storage.conversations.save(conversation)

    async def template_to_patient(self, phone: str, key: str, *, ref: str | None = None, **values: str) -> bool:
        """Send a template and record its text in the conversation, so the agent has context if the patient replies."""
        s = self.services
        message = s.templates.build(key, ref=ref, **values)
        try:
            await s.messaging.send_template(phone, message)
        except MessagingError:
            log.exception("failed to send template %s to %s", key, mask_phone(phone))
            return False
        now = s.now()
        conversation = await s.storage.conversations.get(phone) or Conversation(phone=phone, updated_at=now)
        conversation.add_message("bot", message.preview, now)
        conversation.trim(s.rules.retention.max_messages)
        conversation.expires_at = now + timedelta(days=s.rules.retention.conversation_days)
        await s.storage.conversations.save(conversation)
        return True

    async def to_dentist(self, template_key: str, *, ref: str | None = None, **values: str) -> None:
        """Dentist alerts always use templates: we can't assume her 24h window is open."""
        s = self.services
        try:
            await s.messaging.send_template(
                s.rules.clinic.dentist_phone, s.templates.build(template_key, ref=ref, **values)
            )
        except MessagingError:
            log.exception("failed to notify dentist (%s)", template_key)

    async def reply_dentist(self, text: str) -> None:
        """Reply to a command the dentist just sent (her window is open)."""
        try:
            await self.services.messaging.send_text(self.services.rules.clinic.dentist_phone, text)
        except MessagingError:
            log.exception("failed to reply to dentist")
