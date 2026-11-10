"""HTTP API для сайта: отдаёт клубы/ивенты из бэкенда и принимает заявки с формы.

Эту фабрику использует два разных запуска:
- bot/api_server.py — отдельный процесс, только веб-API, без Telegram (обычный случай,
  когда бота хостит кто-то другой);
- bot/main.py — бот и API в одном процессе, если удобнее держать всё вместе.
"""
import asyncio
import dataclasses
import hmac
import logging
import secrets
from datetime import datetime

from aiogram import Bot
from aiohttp import web

from bot.academics import register_routes as register_academics_routes
from bot.admin import register_routes as register_admin_routes
from bot.content import Content
from bot.db import Database
from bot.hikes import TZ
from bot.hikes import now as hikes_now
from bot.ratelimit import RateLimiter, client_ip, enforce
from bot.web_chat import DEFAULT_REPORTS_TO_BAN, _hash_invite_key, _user_payload, read_json_body, require_session
from bot.web_chat import register_routes as register_web_chat_routes

VALID_ROLES = {"student", "teacher", "registrar", "admin"}
INVITE_KEY_ROLES = {"teacher", "registrar"}

logger = logging.getLogger(__name__)

MAX_FIELD_LEN = 300
LEADS_PER_IP = (10, 60 * 60)
CLEANUP_INTERVAL_SECONDS = 60 * 60
# Тело запроса: base64-файлы заданий/уроков (до 10 МБ) и куски записи экрана (до 8 МБ) плюс
# ~33% накладных расходов base64. Больше не нужно: запись экрана уходит кусками, а не одним файлом.
CLIENT_MAX_SIZE = 24 * 1024 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",          # браузер не «угадывает» тип отданных файлов
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
    "Cross-Origin-Resource-Policy": "cross-origin",
}


def _add_cors_headers(resp: web.StreamResponse, allowed_origin: str) -> None:
    resp.headers["Access-Control-Allow-Origin"] = allowed_origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    # Authorization обязателен: сайт (Vercel) и API (Railway) — разные домены, и без него
    # браузер на preflight блокирует каждый запрос с Bearer-токеном, то есть всё после входа.
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Admin-Token"
    resp.headers["Access-Control-Max-Age"] = "600"


def _add_common_headers(resp: web.StreamResponse, allowed_origin: str) -> None:
    _add_cors_headers(resp, allowed_origin)
    for name, value in SECURITY_HEADERS.items():
        resp.headers.setdefault(name, value)
    # Ответы API содержат личные данные — их нельзя оставлять в кешах браузера и прокси
    # (кроме случаев, когда обработчик сам задал кеширование, например для фото из чата).
    resp.headers.setdefault("Cache-Control", "no-store")


def _cors_middleware(allowed_origin: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
            _add_common_headers(resp, allowed_origin)
            return resp
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            _add_common_headers(exc, allowed_origin)
            raise
        _add_common_headers(resp, allowed_origin)
        return resp

    return middleware


def _require_admin_token(request: web.Request) -> None:
    """Серверный секрет ADMIN_TOKEN: лучше в заголовке X-Admin-Token (адреса с ?token=
    оседают в логах прокси), но параметр в адресе тоже принимается — для старых команд."""
    admin_token = request.app.get("admin_token") or ""
    given = request.headers.get("X-Admin-Token") or request.query.get("token") or ""
    if not admin_token or not hmac.compare_digest(given.encode(), admin_token.encode()):
        raise web.HTTPUnauthorized(reason="invalid or missing token")


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
    """Публичные цифры для главной. Внутренние счётчики (жалобы, баны, заявки) — только
    в админ-панели (/api/admin/overview)."""
    db: Database = request.app["db"]
    s = db.stats()
    return web.json_response({
        "users": s["users"] + s["web_users"],
        "dialogs_total": s["dialogs_total"] + s["web_dialogs"],
        "event_dialogs": s["event_dialogs"] + s["web_event_dialogs"],
    })


# Походы для аккаунтов сайта. В таблице записей ключ — целое число: у Telegram-пользователей
# это их положительный id, поэтому аккаунты сайта записываем под отрицательным (-web_user_id).
def _hike_key(web_user_id: int) -> int:
    return -web_user_id


def _require_hike(db: Database, hike_id: str):
    hike = db.hikes.get(hike_id)
    if hike is None:
        raise web.HTTPNotFound(reason="hike not found")
    return hike


def _my_hike_payload(db: Database, hike, signup) -> dict:
    going, waitlist = db.hikes.counts(hike.id)
    return {
        **hike.public_dict(going, waitlist),
        "my_status": signup.status,
        "waitlist_position": db.hikes.waitlist_position(hike.id, signup.user_id),
        "checked_in": signup.checked_in,
        "active": signup.status == "going" and hike.is_active(hikes_now()),
    }


def _my_hikes(db: Database, user_id: int) -> list[dict]:
    key, at = _hike_key(user_id), hikes_now()
    out = []
    for hike in db.hikes.for_club():
        signup = db.hikes.signup_of(hike.id, key)
        if signup is None or not hike.is_open:
            continue
        if hike.end < at and not hike.is_active(at):
            continue  # давно закончился
        out.append(_my_hike_payload(db, hike, signup))
    return out


async def post_hike_signup(request: web.Request) -> web.Response:
    """Запись на поход одной кнопкой: имя и телефон берутся из аккаунта."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    user = db.get_web_user(user_id)
    if user["role"] not in ("student", "admin"):
        raise web.HTTPForbidden(reason="only student or admin accounts can sign up for hikes")
    hike = _require_hike(db, request.match_info["hike_id"])
    full_name = f"{user['first_name']} {user['last_name']}".strip()
    result = db.hikes.signup(hike.id, _hike_key(user_id), full_name, user["phone"], src="web")
    if result == "closed":
        raise web.HTTPConflict(reason="signup is closed")
    signup = db.hikes.signup_of(hike.id, _hike_key(user_id))
    return web.json_response({"ok": True, "hike": _my_hike_payload(db, hike, signup)})


async def post_hike_cancel(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    hike = _require_hike(db, request.match_info["hike_id"])
    db.hikes.cancel_signup(hike.id, _hike_key(user_id))
    going, waitlist = db.hikes.counts(hike.id)
    return web.json_response({"ok": True, "hike": hike.public_dict(going, waitlist)})


async def post_hike_checkin(request: web.Request) -> web.Response:
    """«Я спустился» — снимает тревогу у организаторов."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    hike = _require_hike(db, request.match_info["hike_id"])
    if not hike.is_active(hikes_now()):
        raise web.HTTPConflict(reason="check-in is available only around the hike time")
    db.hikes.check_in(hike.id, _hike_key(user_id))
    return web.json_response({"ok": True})


async def get_my_hikes(request: web.Request) -> web.Response:
    user_id = require_session(request)
    return web.json_response({"hikes": _my_hikes(request.app["db"], user_id)})


async def get_dashboard(request: web.Request) -> web.Response:
    """Сводка «Сегодня» для главной после входа: пары на сегодня, несданные задания,
    экзамены, последние оценки, непрочитанные сообщения, ивенты и походы."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    content: Content = request.app["content"]
    user = db.get_web_user(user_id)
    today = datetime.now(TZ)
    schedule = db.schedule_for_student(user_id)

    attendance_alerts = []
    for item in schedule:
        stats = db.attendance_stats(item["id"], user_id)
        if stats["total"] and (stats["on_retake"] or stats["ratio"] >= 0.2):
            attendance_alerts.append({"course_code": item["course_code"], "course_title": item["course_title"],
                                      "absent": stats["absent"], "total": stats["total"],
                                      "ratio": stats["ratio"], "on_retake": stats["on_retake"]})

    events = []
    for event_id in db.events_going_for_web_user(user_id):
        event = content.event(event_id)
        if event is not None:
            events.append({"id": event.id, "title": event.title, "date": event.date})

    out = {
        "user": _user_payload(user),
        "weekday": today.weekday(),
        "date": today.date().isoformat(),
        "classes_today": [
            {"course_code": i["course_code"], "course_title": i["course_title"], "start_time": i["start_time"],
             "end_time": i["end_time"], "room": i["room"], "kind": i["kind"]}
            for i in schedule if i["day_of_week"] == today.weekday()],
        "classes_total": len(schedule),
        "deadlines": [
            {"id": a["id"], "course_id": a["course_id"], "course_code": a["course_code"], "title": a["title"],
             "due_at": a["due_at"], "overdue": bool(a["due_at"]) and a["due_at"] < today.date().isoformat()}
            for a in db.pending_assignments_for_student(user_id)[:8]],
        "exams": [{"id": x["id"], "course_code": x["course_code"], "title": x["title"],
                   "time_limit_minutes": x["time_limit_minutes"]} for x in db.pending_exams_for_student(user_id)[:8]],
        "grades": [{"course_code": g["course_code"], "label": g["label"], "score": g["score"],
                    "max_score": g["max_score"]} for g in db.recent_grades_for_student(user_id)],
        "attendance_alerts": attendance_alerts,
        "unread": db.unread_direct_count(user_id),
        "events": events,
        "hikes": _my_hikes(db, user_id),
    }
    if user["role"] in ("teacher", "admin"):
        out["teaching"] = {
            "courses": len(db.list_courses() if user["role"] == "admin" else db.courses_taught_by(user_id)),
            "ungraded": db.ungraded_submissions_count(user_id, is_admin=user["role"] == "admin"),
        }
    return web.json_response(out)


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
    enforce(request, f"leads-ip:{client_ip(request)}", *LEADS_PER_IP)
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
    _require_admin_token(request)
    db: Database = request.app["db"]
    return web.json_response([dict(row) for row in db.recent_leads(50)])


async def admin_reset_web_chat(request: web.Request) -> web.Response:
    """Необратимо стирает ВСЕ аккаунты, диалоги, сообщения и фото «Анонимного чата» —
    для очистки тестовых данных перед демо. POST /api/admin/reset-web-chat?token=<ADMIN_TOKEN>&confirm=yes."""
    _require_admin_token(request)
    if request.query.get("confirm") != "yes":
        raise web.HTTPBadRequest(reason="pass confirm=yes to actually delete everything")
    db: Database = request.app["db"]
    counts = db.reset_web_chat_data()
    return web.json_response({"ok": True, "deleted": counts})


async def admin_set_role(request: web.Request) -> web.Response:
    """Назначает роль аккаунту по почте — так преподаватель/админ учёбы появляется на платформе,
    без отдельного UI для этого. POST /api/admin/set-role?token=<ADMIN_TOKEN>, body {email, role}."""
    _require_admin_token(request)
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
    _require_admin_token(request)
    data = await read_json_body(request)
    role = str(data.get("role", "")).strip().lower()
    if role not in INVITE_KEY_ROLES:
        raise web.HTTPBadRequest(reason=f"role must be one of {sorted(INVITE_KEY_ROLES)}")

    key = secrets.token_urlsafe(18)
    db: Database = request.app["db"]
    db.set_invite_key(role, _hash_invite_key(key))
    return web.json_response({"ok": True, "role": role, "invite_key": key})


async def _cleanup_loop(app: web.Application) -> None:
    """Раз в час чистит истёкшие сессии и коды, старые анонимные переписки и записи экзаменов."""
    while True:
        try:
            removed = {k: v for k, v in app["db"].purge_expired().items() if v}
            if removed:
                logger.info("Плановая очистка: %s", removed)
            app["limiter"].sweep()
        except Exception:
            logger.exception("Ошибка плановой очистки")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


async def _start_cleanup(app: web.Application) -> None:
    app["cleanup_task"] = asyncio.create_task(_cleanup_loop(app))


async def _stop_cleanup(app: web.Application) -> None:
    task = app.get("cleanup_task")
    if task:
        task.cancel()


def build_app(db: Database, content: Content, bot: Bot | None = None,
              admin_ids: frozenset[int] = frozenset(), cors_origin: str = "*",
              admin_token: str = "", brevo_api_key: str = "", brevo_sender_email: str = "",
              brevo_sender_name: str = "Pople", reports_to_ban: int = DEFAULT_REPORTS_TO_BAN) -> web.Application:
    app = web.Application(middlewares=[_cors_middleware(cors_origin)], client_max_size=CLIENT_MAX_SIZE)
    app["db"] = db
    app["content"] = content
    app["bot"] = bot
    app["admin_ids"] = admin_ids
    app["admin_token"] = admin_token
    app["limiter"] = RateLimiter()
    app.on_startup.append(_start_cleanup)
    app.on_cleanup.append(_stop_cleanup)
    app.router.add_get("/health", health)
    app.router.add_get("/api/clubs", get_clubs)
    app.router.add_get("/api/events", get_events)
    app.router.add_get("/api/events/{event_id}", get_event)
    app.router.add_post("/api/events/{event_id}/going", post_event_going)
    app.router.add_get("/api/hikes", get_hikes)
    app.router.add_get("/api/hikes/mine", get_my_hikes)
    app.router.add_get("/api/hikes/{hike_id}", get_hike)
    app.router.add_post("/api/hikes/{hike_id}/signup", post_hike_signup)
    app.router.add_post("/api/hikes/{hike_id}/cancel", post_hike_cancel)
    app.router.add_post("/api/hikes/{hike_id}/checkin", post_hike_checkin)
    app.router.add_get("/api/stats", get_stats)
    app.router.add_get("/api/dashboard", get_dashboard)
    app.router.add_post("/api/leads", post_lead)
    app.router.add_get("/api/leads", get_leads)
    app.router.add_post("/api/admin/reset-web-chat", admin_reset_web_chat)
    app.router.add_post("/api/admin/set-role", admin_set_role)
    app.router.add_post("/api/admin/rotate-invite-key", admin_rotate_invite_key)
    register_admin_routes(app)
    register_academics_routes(app)
    register_web_chat_routes(app, brevo_api_key=brevo_api_key, brevo_sender_email=brevo_sender_email,
                             brevo_sender_name=brevo_sender_name, reports_to_ban=reports_to_ban)
    return app
