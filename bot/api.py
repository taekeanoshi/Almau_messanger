"""HTTP API для сайта: отдаёт клубы/ивенты из бэкенда и принимает заявки с формы.

Работает в том же процессе, что и Telegram-бот (см. main.py), поэтому для хостинга
на Railway достаточно одного сервиса: он слушает Telegram через long polling
и параллельно поднимает веб-сервер на $PORT для сайта.
"""
import dataclasses
import logging

from aiogram import Bot
from aiohttp import web

from bot.content import Content
from bot.db import Database

logger = logging.getLogger(__name__)

MAX_FIELD_LEN = 300


def _add_cors_headers(resp: web.StreamResponse, allowed_origin: str) -> None:
    resp.headers["Access-Control-Allow-Origin"] = allowed_origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"


def _cors_middleware(allowed_origin: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
            _add_cors_headers(resp, allowed_origin)
            return resp
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            _add_cors_headers(exc, allowed_origin)
            raise
        _add_cors_headers(resp, allowed_origin)
        return resp

    return middleware


def _event_payload(content: Content, db: Database, event_id: str) -> dict | None:
    ev = content.event(event_id)
    if ev is None:
        return None
    return {**dataclasses.asdict(ev), "going": db.going_count(event_id)}


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def get_clubs(request: web.Request) -> web.Response:
    content: Content = request.app["content"]
    return web.json_response([dataclasses.asdict(c) for c in content.clubs])


async def get_events(request: web.Request) -> web.Response:
    content: Content = request.app["content"]
    db: Database = request.app["db"]
    return web.json_response([_event_payload(content, db, e.id) for e in content.events])


async def get_event(request: web.Request) -> web.Response:
    content: Content = request.app["content"]
    db: Database = request.app["db"]
    payload = _event_payload(content, db, request.match_info["event_id"])
    if payload is None:
        raise web.HTTPNotFound(reason="event not found")
    return web.json_response(payload)


async def get_stats(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    return web.json_response(db.stats())


async def _notify_admins_of_lead(app: web.Application, name: str, contact: str,
                                  message: str, event_id: str | None) -> None:
    bot: Bot | None = app.get("bot")
    admin_ids = app.get("admin_ids") or ()
    if bot is None:
        return
    extra = f"\nИвент: {event_id}" if event_id else ""
    quote = f"\n«{message}»" if message else ""
    text = f"📝 Новая заявка с сайта\n{name} · {contact}{extra}{quote}"
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # админ мог не запускать бота — приём заявки не должен из-за этого падать
            logger.warning("Не удалось уведомить админа %s о заявке", admin_id)


async def post_lead(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(reason="invalid json")

    name = str(data.get("name", "")).strip()[:MAX_FIELD_LEN]
    contact = str(data.get("contact", "")).strip()[:MAX_FIELD_LEN]
    message = str(data.get("message", "")).strip()[:MAX_FIELD_LEN]
    event_id = str(data.get("event_id", "")).strip()[:MAX_FIELD_LEN] or None
    if not name or not contact:
        raise web.HTTPBadRequest(reason="name and contact are required")

    lead_id = db.add_lead(name, contact, message, event_id)
    await _notify_admins_of_lead(request.app, name, contact, message, event_id)
    return web.json_response({"ok": True, "id": lead_id}, status=201)


def build_app(db: Database, content: Content, bot: Bot | None = None,
              admin_ids: frozenset[int] = frozenset(), cors_origin: str = "*") -> web.Application:
    app = web.Application(middlewares=[_cors_middleware(cors_origin)])
    app["db"] = db
    app["content"] = content
    app["bot"] = bot
    app["admin_ids"] = admin_ids
    app.router.add_get("/health", health)
    app.router.add_get("/api/clubs", get_clubs)
    app.router.add_get("/api/events", get_events)
    app.router.add_get("/api/events/{event_id}", get_event)
    app.router.add_get("/api/stats", get_stats)
    app.router.add_post("/api/leads", post_lead)
    return app
