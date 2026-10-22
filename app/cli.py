"""Command line: ``clinic chat`` (terminal chat), ``clinic eval`` (scenario evals), ``clinic job`` and
``clinic ms-login`` (Microsoft refresh token via device code).

``clinic chat`` always uses in-memory storage and a fake WhatsApp, so nothing is sent to real patients. The calendar
is fake by default (seeded with a few events) but honours ``CALENDAR_PROVIDER`` if you want to try a real one.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import sys
from datetime import UTC, datetime, time, timedelta

from app.adapters.calendar.fake import FakeCalendar
from app.adapters.messaging.base import InboundMessage
from app.adapters.messaging.fake import FakeMessaging
from app.adapters.storage.memory import InMemoryStorage
from app.config import Settings
from app.container import ConfigurationError, Container, build_container

DEMO_PATIENT = "5581999990000"


def _seed_calendar(calendar: FakeCalendar, settings_tz_now: datetime) -> None:
    """A few busy blocks over the next days so the demo looks realistic."""
    tz = settings_tz_now.tzinfo
    for offset, (start_h, end_h) in zip(range(1, 6), [(9, 10), (14, 16), (8, 9), (10, 11), (15, 17)], strict=True):
        day = (settings_tz_now + timedelta(days=offset)).date()
        calendar.add_busy(
            datetime.combine(day, time(start_h), tzinfo=tz),
            datetime.combine(day, time(end_h), tzinfo=tz),
            "Paciente (exemplo)",
        )


def _build_for_cli(args: argparse.Namespace) -> tuple[Container, FakeMessaging]:
    settings = Settings()
    if args.model:
        settings.agent_model = args.model
    messaging = FakeMessaging()
    calendar = FakeCalendar() if settings.calendar_provider == "fake" else None
    container = build_container(settings, storage=InMemoryStorage(), messaging=messaging, calendar=calendar)
    if isinstance(calendar, FakeCalendar):
        _seed_calendar(calendar, container.services.now())
    return container, messaging


def _print_outbox(messaging: FakeMessaging, start: int, dentist_phone: str) -> int:
    for msg in messaging.outbox[start:]:
        who = "Doutora" if msg.to == dentist_phone else "Paciente"
        if msg.kind == "template" and msg.template is not None:
            buttons = f" [botões: {', '.join(msg.template.button_payloads)}]" if msg.template.button_payloads else ""
            print(f"\n  📨 template '{msg.template.name}' → {who}: {msg.text}{buttons}")
        elif who == "Paciente":
            print(f"\n🦷 Bot: {msg.text}")
        else:
            print(f"\n  📨 → Doutora: {msg.text}")
    return len(messaging.outbox)


async def _chat(args: argparse.Namespace) -> int:
    container, messaging = _build_for_cli(args)
    rules = container.rules
    ids = itertools.count(1)
    print(
        f"Conversando com o assistente de '{rules.clinic.name}' (modelo: {container.settings.agent_model}).\n"
        "Comandos: /doutora <texto> (mensagem da dentista, ex.: '/doutora #ok 1'), /botao <payload>, "
        "/job <nome>, /estado, /sair\n"
    )
    seen = 0
    while True:
        try:
            line = (await asyncio.to_thread(input, "\n🙂 Você: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/sair", "/exit", "/quit"):
            return 0
        if line == "/estado":
            conv = await container.services.storage.conversations.get(DEMO_PATIENT)
            if conv:
                print(f"  estado={conv.state.value} turno={conv.turn} horários={list(conv.offered_slots)}")
                print(f"  proposta={conv.pending_action.summary if conv.pending_action else None}")
            continue
        if line.startswith("/job "):
            name = line.split(maxsplit=1)[1]
            handler = container.jobs.get(name)
            print(f"  {await handler(container.services)}" if handler else f"  jobs: {', '.join(container.jobs)}")
            seen = _print_outbox(messaging, seen, rules.clinic.dentist_phone)
            continue

        phone, text, button = DEMO_PATIENT, line, None
        if line.startswith("/doutora "):
            phone, text = rules.clinic.dentist_phone, line.split(maxsplit=1)[1]
        elif line.startswith("/botao "):
            text, button = "", line.split(maxsplit=1)[1]
        await container.orchestrator.handle(
            InboundMessage(
                message_id=f"cli-{next(ids)}",
                from_phone=phone,
                timestamp=datetime.now(UTC),
                text=text,
                button_payload=button,
            )
        )
        seen = _print_outbox(messaging, seen, rules.clinic.dentist_phone)


async def _job(args: argparse.Namespace) -> int:
    container, messaging = _build_for_cli(args)
    handler = container.jobs.get(args.name)
    if handler is None:
        print(f"Job desconhecido. Disponíveis: {', '.join(container.jobs)}", file=sys.stderr)
        return 2
    print(await handler(container.services))
    _print_outbox(messaging, 0, container.rules.clinic.dentist_phone)
    return 0


async def _eval(args: argparse.Namespace) -> int:
    from evals.runner import run_evals

    return await run_evals(model=args.model, scenarios_dir=args.scenarios, only=args.only, verbose=args.verbose)


async def _ms_login(args: argparse.Namespace) -> int:
    from app.adapters.calendar.microsoft import device_code_login

    settings = Settings()
    if not settings.ms_client_id:
        print("Defina MS_CLIENT_ID (app registrado no Azure/Entra) antes de rodar.", file=sys.stderr)
        return 2
    token = await device_code_login(client_id=settings.ms_client_id, tenant_id=settings.ms_tenant_id)
    print("\nGuarde este valor como segredo MS_REFRESH_TOKEN (não commite!):\n")
    print(token)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clinic", description="Assistente de agendamento do consultório.")
    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="conversar com o assistente no terminal (fakes, nada é enviado)")
    chat.add_argument("--model", help="ex.: test, openrouter:<modelo>, anthropic:<modelo> (padrão: AGENT_MODEL)")

    job = sub.add_parser("job", help="rodar um job localmente (fakes)")
    job.add_argument("name")
    job.add_argument("--model", default="test")

    ev = sub.add_parser("eval", help="rodar os cenários de avaliação")
    ev.add_argument("--model", required=True, help="'test' (roteiro, sem LLM) ou um modelo real")
    ev.add_argument("--scenarios", default="evals/scenarios")
    ev.add_argument("--only", help="rodar só cenários cujo nome contém este texto")
    ev.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("ms-login", help="obter MS_REFRESH_TOKEN (Outlook pessoal) via device code")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    commands = {"chat": _chat, "job": _job, "eval": _eval, "ms-login": _ms_login}
    try:
        return asyncio.run(commands[args.command](args))
    except ConfigurationError as exc:
        print(f"Configuração incompleta: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
