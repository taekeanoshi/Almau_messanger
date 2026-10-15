"""Анонимный чат прямо на сайте — независимо от Telegram-бота.

Свои аккаунты (имя, фамилия, телефон, почта), свой пол и подбор собеседника
по полу («такой же» / «противоположный» / «любой»), свои диалоги и сообщения.
Все данные — в таблицах web_* (bot/db.py), отдельно от таблиц бота.

Вход — без пароля: одноразовый 6-значный код на почту (через Brevo), действует
10 минут. Если BREVO_API_KEY не задан (например, локально), код просто пишется
в лог — почту слать некуда, но проверить флоу можно.

Подбор собеседника держим в памяти процесса (WebMatchmaker), как и в боте
(bot/matchmaking.py): очередь и пары меняются каждую секунду, в БД их хранить
не нужно. Диалоги и сообщения — в БД, чтобы их можно было отдавать через
поллинг (GET /api/chat/poll) по мере прихода новых сообщений.
"""
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

from bot.db import Database

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 80
MAX_MESSAGE_LEN = 2000
GENDERS = {"male", "female"}
PREFERENCES = {"any", "same", "opposite"}

OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit() or ch == "+")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _otp_expiry_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS)).isoformat(timespec="seconds")


def _generate_otp() -> str:
    return str(secrets.randbelow(900_000) + 100_000)


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


async def _send_otp_email(app: web.Application, to_email: str, code: str) -> None:
    api_key = app.get("brevo_api_key") or ""
    sender_email = app.get("brevo_sender_email") or ""
    if not api_key or not sender_email:
        logger.warning("BREVO_API_KEY или BREVO_SENDER_EMAIL не заданы — код для %s: %s (письмо не отправлено)",
                       to_email, code)
        return
    payload = {
        "sender": {"name": app.get("brevo_sender_name") or "Almau Messenger", "email": sender_email},
        "to": [{"email": to_email}],
        "subject": "Код входа в Almau Messenger",
        "htmlContent": (f"<p>Твой код для входа: <b>{code}</b></p>"
                        f"<p>Код действует 10 минут. Если это были не вы — просто проигнорируй письмо.</p>"),
    }
    headers = {"api-key": api_key, "Content-Type": "application/json", "accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(BREVO_API_URL, json=payload, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status >= 300:
                    logger.error("Brevo отказал в отправке письма (%s): %s", resp.status, await resp.text())
    except Exception:
        logger.exception("Не удалось отправить письмо с кодом через Brevo")


class WebMatchmaker:
    """Очередь поиска и подбор пар по полу. В памяти процесса, без БД — как Matchmaker у бота."""

    def __init__(self) -> None:
        self._queue: list[dict] = []
        self._pairs: dict[int, dict] = {}

    @staticmethod
    def _wants(preference: str, my_gender: str, other_gender: str) -> bool:
        if preference == "same":
            return my_gender == other_gender
        if preference == "opposite":
            return my_gender != other_gender
        return True  # "any"

    def _compatible(self, a: dict, b: dict) -> bool:
        return (self._wants(a["preference"], a["gender"], b["gender"])
                and self._wants(b["preference"], b["gender"], a["gender"]))

    def search(self, user_id: int, gender: str, preference: str) -> dict:
        """Ищет подходящего собеседника в очереди; если не нашёл — встаёт в очередь сам."""
        self.leave(user_id)
        me = {"user_id": user_id, "gender": gender, "preference": preference}
        for i, other in enumerate(self._queue):
            if self._compatible(me, other):
                self._queue.pop(i)
                dialog_id = secrets.token_hex(8)
                self._pairs[user_id] = {"partner_id": other["user_id"], "dialog_id": dialog_id}
                self._pairs[other["user_id"]] = {"partner_id": user_id, "dialog_id": dialog_id}
                return {"status": "matched", "dialog_id": dialog_id, "partner_id": other["user_id"]}
        self._queue.append(me)
        return {"status": "waiting"}

    def status(self, user_id: int) -> dict:
        pair = self._pairs.get(user_id)
        if pair:
            return {"status": "matched", "dialog_id": pair["dialog_id"], "partner_id": pair["partner_id"]}
        if any(x["user_id"] == user_id for x in self._queue):
            return {"status": "waiting"}
        return {"status": "idle"}

    def leave(self, user_id: int) -> int | None:
        """Убирает из очереди/пары. Возвращает id собеседника, если был в паре — чтобы его тоже отцепить."""
        self._queue[:] = [x for x in self._queue if x["user_id"] != user_id]
        pair = self._pairs.pop(user_id, None)
        if pair is None:
            return None
        partner_id = pair["partner_id"]
        self._pairs.pop(partner_id, None)
        return partner_id


async def read_json_body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(reason="invalid json")
    return data


def require_session(request: web.Request) -> int:
    """id веб-пользователя по заголовку Authorization: Bearer <token>."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise web.HTTPUnauthorized(reason="missing token")
    db: Database = request.app["db"]
    user_id = db.web_session_user(token)
    if user_id is None:
        raise web.HTTPUnauthorized(reason="invalid session")
    return user_id


async def register(request: web.Request) -> web.Response:
    data = await read_json_body(request)
    first_name = str(data.get("first_name", "")).strip()[:MAX_NAME_LEN]
    last_name = str(data.get("last_name", "")).strip()[:MAX_NAME_LEN]
    phone = _normalize_phone(str(data.get("phone", "")))
    email = _normalize_email(str(data.get("email", "")))
    gender = str(data.get("gender", "")).strip().lower()

    if not first_name or not last_name:
        raise web.HTTPBadRequest(reason="first_name and last_name are required")
    if len(phone) < 7:
        raise web.HTTPBadRequest(reason="invalid phone")
    if not _valid_email(email):
        raise web.HTTPBadRequest(reason="invalid email")
    if gender not in GENDERS:
        raise web.HTTPBadRequest(reason="gender must be male or female")

    db: Database = request.app["db"]
    if db.get_web_user_by_phone(phone) is not None or db.get_web_user_by_email(email) is not None:
        raise web.HTTPConflict(reason="phone or email already registered")

    user_id = db.create_web_user(first_name, last_name, phone, email, gender)
    token = secrets.token_urlsafe(32)
    db.create_web_session(token, user_id)
    return web.json_response(
        {"ok": True, "token": token, "user": {"id": user_id, "first_name": first_name, "gender": gender}},
        status=201,
    )


async def otp_request(request: web.Request) -> web.Response:
    """Шаг 1 входа: сгенерировать код и отправить на почту (Brevo)."""
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    if not _valid_email(email):
        raise web.HTTPBadRequest(reason="invalid email")

    db: Database = request.app["db"]
    row = db.get_web_user_by_email(email)
    if row is None:
        raise web.HTTPNotFound(reason="email not registered")
    if row["banned"]:
        raise web.HTTPForbidden(reason="banned")

    code = _generate_otp()
    db.save_otp_code(email, _hash_otp(code), _otp_expiry_iso())
    await _send_otp_email(request.app, email, code)
    return web.json_response({"ok": True})


async def otp_verify(request: web.Request) -> web.Response:
    """Шаг 2 входа: проверить код, выдать токен сессии."""
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    code = str(data.get("code", "")).strip()

    db: Database = request.app["db"]
    otp = db.get_otp_code(email)
    if otp is None or otp["expires_at"] < _now_iso():
        raise web.HTTPUnauthorized(reason="code expired or not requested")
    if otp["attempts"] >= OTP_MAX_ATTEMPTS:
        raise web.HTTPForbidden(reason="too many attempts, request a new code")
    if not code or not hmac.compare_digest(otp["code_hash"], _hash_otp(code)):
        db.bump_otp_attempts(email)
        raise web.HTTPUnauthorized(reason="invalid code")

    row = db.get_web_user_by_email(email)
    if row is None:
        raise web.HTTPNotFound(reason="user not found")
    db.clear_otp_code(email)

    token = secrets.token_urlsafe(32)
    db.create_web_session(token, row["id"])
    return web.json_response(
        {"ok": True, "token": token, "user": {"id": row["id"], "first_name": row["first_name"], "gender": row["gender"]}}
    )


async def logout(request: web.Request) -> web.Response:
    user_id = require_session(request)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    request.app["db"].delete_web_session(token)
    request.app["web_mm"].leave(user_id)
    return web.json_response({"ok": True})


async def me(request: web.Request) -> web.Response:
    user_id = require_session(request)
    row = request.app["db"].get_web_user(user_id)
    if row is None:
        raise web.HTTPUnauthorized(reason="user not found")
    return web.json_response({"id": row["id"], "first_name": row["first_name"], "gender": row["gender"]})


def _start_search(request: web.Request, user_id: int, preference: str) -> dict:
    if preference not in PREFERENCES:
        raise web.HTTPBadRequest(reason="preference must be any, same or opposite")
    db: Database = request.app["db"]
    row = db.get_web_user(user_id)
    mm: WebMatchmaker = request.app["web_mm"]
    result = mm.search(user_id, row["gender"], preference)
    if result["status"] == "matched":
        db.create_web_dialog(result["dialog_id"], user_id, result["partner_id"])
    return result


async def search(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    preference = str(data.get("preference", "any")).strip().lower()
    return web.json_response(_start_search(request, user_id, preference))


async def poll(request: web.Request) -> web.Response:
    user_id = require_session(request)
    mm: WebMatchmaker = request.app["web_mm"]
    state = mm.status(user_id)
    if state["status"] != "matched":
        return web.json_response(state)

    db: Database = request.app["db"]
    since = int(request.query.get("since", "0") or "0")
    messages = db.web_messages_since(state["dialog_id"], since)
    return web.json_response({
        "status": "matched",
        "dialog_id": state["dialog_id"],
        "messages": [{"id": m["id"], "mine": m["sender_id"] == user_id, "text": m["text"]} for m in messages],
    })


async def send(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    text = str(data.get("text", "")).strip()[:MAX_MESSAGE_LEN]
    if not text:
        raise web.HTTPBadRequest(reason="text is required")

    mm: WebMatchmaker = request.app["web_mm"]
    state = mm.status(user_id)
    if state["status"] != "matched" or state["dialog_id"] != dialog_id:
        raise web.HTTPConflict(reason="not in this dialog")

    msg_id = request.app["db"].add_web_message(dialog_id, user_id, text)
    return web.json_response({"ok": True, "id": msg_id})


async def _leave(request: web.Request, requeue: bool) -> web.Response:
    user_id = require_session(request)
    request.app["web_mm"].leave(user_id)
    if not requeue:
        return web.json_response({"status": "idle"})

    preference = "any"
    if request.can_read_body:
        try:
            data = await request.json()
            if isinstance(data, dict):
                preference = str(data.get("preference", "any")).strip().lower()
        except ValueError:
            pass
    if preference not in PREFERENCES:
        preference = "any"
    return web.json_response(_start_search(request, user_id, preference))


async def chat_next(request: web.Request) -> web.Response:
    return await _leave(request, requeue=True)


async def chat_stop(request: web.Request) -> web.Response:
    return await _leave(request, requeue=False)


def register_routes(app: web.Application, brevo_api_key: str = "", brevo_sender_email: str = "",
                     brevo_sender_name: str = "Almau Messenger") -> None:
    app["web_mm"] = WebMatchmaker()
    app["brevo_api_key"] = brevo_api_key
    app["brevo_sender_email"] = brevo_sender_email
    app["brevo_sender_name"] = brevo_sender_name
    app.router.add_post("/api/chat/register", register)
    app.router.add_post("/api/chat/otp/request", otp_request)
    app.router.add_post("/api/chat/otp/verify", otp_verify)
    app.router.add_post("/api/chat/logout", logout)
    app.router.add_get("/api/chat/me", me)
    app.router.add_post("/api/chat/search", search)
    app.router.add_get("/api/chat/poll", poll)
    app.router.add_post("/api/chat/send", send)
    app.router.add_post("/api/chat/next", chat_next)
    app.router.add_post("/api/chat/stop", chat_stop)
