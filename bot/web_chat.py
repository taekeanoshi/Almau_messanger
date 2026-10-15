"""Анонимный чат прямо на сайте — независимо от Telegram-бота.

Свои аккаунты (имя, фамилия, телефон, пароль), свой пол и подбор собеседника
по полу («такой же» / «противоположный» / «любой»), свои диалоги и сообщения.
Все данные — в таблицах web_* (bot/db.py), отдельно от таблиц бота.

Подбор собеседника держим в памяти процесса (WebMatchmaker), как и в боте
(bot/matchmaking.py): очередь и пары меняются каждую секунду, в БД их хранить
не нужно. Диалоги и сообщения — в БД, чтобы их можно было отдавать через
поллинг (GET /api/chat/poll) по мере прихода новых сообщений.
"""
import hashlib
import hmac
import logging
import secrets

from aiohttp import web

from bot.db import Database

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 80
MAX_MESSAGE_LEN = 2000
MIN_PASSWORD_LEN = 6
GENDERS = {"male", "female"}
PREFERENCES = {"any", "same", "opposite"}


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return digest, salt


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest, _ = _hash_password(password, salt)
    return hmac.compare_digest(digest, expected_hash)


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit() or ch == "+")


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
    password = str(data.get("password", ""))
    gender = str(data.get("gender", "")).strip().lower()

    if not first_name or not last_name:
        raise web.HTTPBadRequest(reason="first_name and last_name are required")
    if len(phone) < 7:
        raise web.HTTPBadRequest(reason="invalid phone")
    if len(password) < MIN_PASSWORD_LEN:
        raise web.HTTPBadRequest(reason=f"password must be at least {MIN_PASSWORD_LEN} characters")
    if gender not in GENDERS:
        raise web.HTTPBadRequest(reason="gender must be male or female")

    db: Database = request.app["db"]
    if db.get_web_user_by_phone(phone) is not None:
        raise web.HTTPConflict(reason="phone already registered")

    password_hash, salt = _hash_password(password)
    user_id = db.create_web_user(first_name, last_name, phone, password_hash, salt, gender)
    token = secrets.token_urlsafe(32)
    db.create_web_session(token, user_id)
    return web.json_response(
        {"ok": True, "token": token, "user": {"id": user_id, "first_name": first_name, "gender": gender}},
        status=201,
    )


async def login(request: web.Request) -> web.Response:
    data = await read_json_body(request)
    phone = _normalize_phone(str(data.get("phone", "")))
    password = str(data.get("password", ""))

    db: Database = request.app["db"]
    row = db.get_web_user_by_phone(phone)
    if row is None or not _verify_password(password, row["password_salt"], row["password_hash"]):
        raise web.HTTPUnauthorized(reason="invalid phone or password")
    if row["banned"]:
        raise web.HTTPForbidden(reason="banned")

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


def register_routes(app: web.Application) -> None:
    app["web_mm"] = WebMatchmaker()
    app.router.add_post("/api/chat/register", register)
    app.router.add_post("/api/chat/login", login)
    app.router.add_post("/api/chat/logout", logout)
    app.router.add_get("/api/chat/me", me)
    app.router.add_post("/api/chat/search", search)
    app.router.add_get("/api/chat/poll", poll)
    app.router.add_post("/api/chat/send", send)
    app.router.add_post("/api/chat/next", chat_next)
    app.router.add_post("/api/chat/stop", chat_stop)
