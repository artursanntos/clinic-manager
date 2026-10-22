"""FastAPI app: WhatsApp webhook, scheduled jobs and health check.

Run locally: ``uv run uvicorn app.main:create_app --factory --reload``
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from app.adapters.messaging.whatsapp_cloud import parse_webhook, verify_signature
from app.config import Settings
from app.container import ConfigurationError, Container, build_container

log = logging.getLogger(__name__)


def create_app(container: Container | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or (container.settings if container else Settings())
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    if settings.messaging_provider == "whatsapp_cloud" and (
        settings.whatsapp_app_secret is None or settings.whatsapp_verify_token is None
    ):
        raise ConfigurationError("WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN are required for whatsapp_cloud")

    state: dict[str, Container] = {}
    if container is not None:
        state["container"] = container

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if "container" not in state:
            state["container"] = build_container(settings)
        yield

    app = FastAPI(title="clinic-manager", lifespan=lifespan, docs_url=None, redoc_url=None)

    def get_container() -> Container:
        if "container" not in state:  # e.g. TestClient used without lifespan
            state["container"] = build_container(settings)
        return state["container"]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/webhook")
    async def verify_webhook(
        mode: Annotated[str | None, Query(alias="hub.mode")] = None,
        token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
        challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
    ) -> Response:
        expected = settings.whatsapp_verify_token.get_secret_value() if settings.whatsapp_verify_token else None
        if mode == "subscribe" and expected and token and hmac.compare_digest(token, expected) and challenge:
            return PlainTextResponse(challenge)
        raise HTTPException(status_code=403, detail="verification failed")

    @app.post("/webhook")
    async def receive_webhook(
        request: Request,
        signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    ) -> dict[str, Any]:
        body = await request.body()
        if settings.whatsapp_app_secret is not None:
            if not verify_signature(settings.whatsapp_app_secret.get_secret_value(), body, signature):
                raise HTTPException(status_code=403, detail="invalid signature")
        elif settings.messaging_provider != "fake":
            raise HTTPException(status_code=403, detail="signature verification not configured")
        try:
            payload = json.loads(body or b"{}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid json") from exc

        orchestrator = get_container().orchestrator
        handled = 0
        # Processed inside the request on purpose (Cloud Run throttles CPU after the response). Dedupe covers retries.
        for message in parse_webhook(payload):
            try:
                await orchestrator.handle(message)
                handled += 1
            except Exception:
                # Always 200: the message id is already claimed, so a Meta retry would be dropped anyway.
                log.exception("failed to handle inbound message")
        return {"status": "ok", "handled": handled}

    @app.post("/jobs/{name}")
    async def run_job(name: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        if settings.jobs_token is None:
            raise HTTPException(status_code=503, detail="jobs disabled (JOBS_TOKEN not set)")
        expected = f"Bearer {settings.jobs_token.get_secret_value()}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="unauthorized")
        container = get_container()
        handler = container.jobs.get(name)
        if handler is None:
            raise HTTPException(status_code=404, detail=f"unknown job; available: {sorted(container.jobs)}")
        result = await handler(container.services)
        return asdict(result)

    return app
