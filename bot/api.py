"""HTTP API для сайта: отдаёт клубы/ивенты из бэкенда и принимает заявки с формы.

Эту фабрику использует два разных запуска:
- bot/api_server.py — отдельный процесс, только веб-API, без Telegram (обычный случай,
  когда бота хостит кто-то другой);
- bot/main.py — бот и API в одном процессе, если удобнее держать всё вместе.
"""
import dataclasses
import logging
import secrets

from aiogram import Bot
from aiohttp import web

from bot.academics import register_routes as register_academics_routes
from bot.content import Content
from bot.db import Database
from bot.web_chat import _hash_invite_key, read_json_body, require_session
from bot.web_chat import register_routes as register_web_chat_routes

VALID_ROLES = {"student", "teacher", "registrar", "admin"}
INVITE_KEY_ROLES = {"teacher", "registrar"}

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
    """mode показывает, что запущено на этом домене: api-only (без бота) или bot+api."""
    bot: Bot | None = request.app.get("bot")
    return web.json_response({"status": "ok", "mode": "bot+api" if bot else "api-only"})


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


def _hike_payloads(db: Database, club: str | None) -> list[dict]:
    out = []
    for h in db.hikes.upcoming(club):
        going, waitlist = db.hikes.counts(h.id)
        out.append(h.public_dict(going, waitlist))
    return out


async def get_hikes(request: web.Request) -> web.Response:
    """Ближайшие походы с живым числом свободных мест. Имён и телефонов здесь нет."""
    db: Database = request.app["db"]
    return web.json_response(_hike_payloads(db, request.query.get("club") or None))


async def get_hike(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    hike = db.hikes.get(request.match_info["hike_id"])
    if hike is None:
        raise web.HTTPNotFound(reason="hike not found")
    going, waitlist = db.hikes.counts(hike.id)
    return web.json_response(hike.public_dict(going, waitlist))


async def get_stats(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    return web.json_response(db.stats())


async def post_event_going(request: web.Request) -> web.Response:
    """Отметка «Иду» доступна только зарегистрированным в веб-чате пользователям сайта
    (require_session бросит 401 без валидного токена). Это готовит данные к тому, чтобы
    позже показывать клубам-организаторам реальные имена идущих, а не анонимные визиты."""
    content: Content = request.app["content"]
    db: Database = request.app["db"]
    event_id = request.match_info["event_id"]
    if content.event(event_id) is None:
        raise web.HTTPNotFound(reason="event not found")

    user_id = require_session(request)
    if db.get_web_user(user_id)["role"] not in ("student", "admin"):
        raise web.HTTPForbidden(reason="only student or admin accounts can mark events")
    data = await read_json_body(request)
    going = bool(data.get("going", True))

    db.set_going(f"webuser:{user_id}", event_id, going)
    return web.json_response({"ok": True, "going": going, "count": db.going_count(event_id)})


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


async def get_leads(request: web.Request) -> web.Response:
    """Просмотр заявок без бота: GET /api/leads?token=<ADMIN_TOKEN>.
    Нужен, когда API задеплоен отдельно от бота и команды /leads в Telegram нет."""
    admin_token = request.app.get("admin_token")
    if not admin_token or request.query.get("token") != admin_token:
        raise web.HTTPUnauthorized(reason="invalid or missing token")
    db: Database = request.app["db"]
    return web.json_response([dict(row) for row in db.recent_leads(50)])


async def admin_reset_web_chat(request: web.Request) -> web.Response:
    """Необратимо стирает ВСЕ аккаунты, диалоги, сообщения и фото «Анонимного чата» —
    для очистки тестовых данных перед демо. POST /api/admin/reset-web-chat?token=<ADMIN_TOKEN>&confirm=yes."""
    admin_token = request.app.get("admin_token")
    if not admin_token or request.query.get("token") != admin_token:
        raise web.HTTPUnauthorized(reason="invalid or missing token")
    if request.query.get("confirm") != "yes":
        raise web.HTTPBadRequest(reason="pass confirm=yes to actually delete everything")
    db: Database = request.app["db"]
    counts = db.reset_web_chat_data()
    return web.json_response({"ok": True, "deleted": counts})


async def admin_set_role(request: web.Request) -> web.Response:
    """Назначает роль аккаунту по почте — так преподаватель/админ учёбы появляется на платформе,
    без отдельного UI для этого. POST /api/admin/set-role?token=<ADMIN_TOKEN>, body {email, role}."""
    admin_token = request.app.get("admin_token")
    if not admin_token or request.query.get("token") != admin_token:
        raise web.HTTPUnauthorized(reason="invalid or missing token")
    data = await read_json_body(request)
    email = str(data.get("email", "")).strip().lower()
    role = str(data.get("role", "")).strip().lower()
    if role not in VALID_ROLES:
        raise web.HTTPBadRequest(reason=f"role must be one of {sorted(VALID_ROLES)}")
    db: Database = request.app["db"]
    if not db.set_web_user_role(email, role):
        raise web.HTTPNotFound(reason="no account with this email")
    return web.json_response({"ok": True, "email": email, "role": role})


async def admin_rotate_invite_key(request: web.Request) -> web.Response:
    """Генерирует новый ключ-приглашение для самостоятельной регистрации преподавателя
    или отдела расписания — старый ключ сразу перестаёт работать. Сырой ключ отдаётся
    только в этом ответе и нигде не хранится — потеряли, значит, рожаем новый.
    POST /api/admin/rotate-invite-key?token=<ADMIN_TOKEN>, body {"role": "teacher"|"registrar"}."""
    admin_token = request.app.get("admin_token")
    if not admin_token or request.query.get("token") != admin_token:
        raise web.HTTPUnauthorized(reason="invalid or missing token")
    data = await read_json_body(request)
    role = str(data.get("role", "")).strip().lower()
    if role not in INVITE_KEY_ROLES:
        raise web.HTTPBadRequest(reason=f"role must be one of {sorted(INVITE_KEY_ROLES)}")

    key = secrets.token_urlsafe(18)
    db: Database = request.app["db"]
    db.set_invite_key(role, _hash_invite_key(key))
    return web.json_response({"ok": True, "role": role, "invite_key": key})


def build_app(db: Database, content: Content, bot: Bot | None = None,
              admin_ids: frozenset[int] = frozenset(), cors_origin: str = "*",
              admin_token: str = "", brevo_api_key: str = "", brevo_sender_email: str = "",
              brevo_sender_name: str = "Pople") -> web.Application:
    # client_max_size поднят под base64-файлы: фото в чате (MAX_PHOTO_BYTES в web_chat.py)
    # и файлы заданий/сдач (MAX_FILE_BYTES в academics.py, до 10 МБ) + накладные расходы
    # base64/JSON — дефолтных 1 МБ aiohttp на это не хватит.
    app = web.Application(middlewares=[_cors_middleware(cors_origin)], client_max_size=16 * 1024 * 1024)
    app["db"] = db
    app["content"] = content
    app["bot"] = bot
    app["admin_ids"] = admin_ids
    app["admin_token"] = admin_token
    app.router.add_get("/health", health)
    app.router.add_get("/api/clubs", get_clubs)
    app.router.add_get("/api/events", get_events)
    app.router.add_get("/api/events/{event_id}", get_event)
    app.router.add_post("/api/events/{event_id}/going", post_event_going)
    app.router.add_get("/api/hikes", get_hikes)
    app.router.add_get("/api/hikes/{hike_id}", get_hike)
    app.router.add_get("/api/stats", get_stats)
    app.router.add_post("/api/leads", post_lead)
    app.router.add_get("/api/leads", get_leads)
    app.router.add_post("/api/admin/reset-web-chat", admin_reset_web_chat)
    app.router.add_post("/api/admin/set-role", admin_set_role)
    app.router.add_post("/api/admin/rotate-invite-key", admin_rotate_invite_key)
    register_academics_routes(app)
    register_web_chat_routes(app, brevo_api_key=brevo_api_key, brevo_sender_email=brevo_sender_email,
                             brevo_sender_name=brevo_sender_name)
    return app
