"""Аккаунты и чаты прямо на сайте — независимо от Telegram-бота.

Аккаунты: имя, фамилия, телефон, почта, пароль (PBKDF2). Почта подтверждается один раз
6-значным кодом (через Brevo, действует 10 минут); тем же механизмом работает сброс
забытого пароля. Если BREVO_API_KEY не задан (например, локально), код просто пишется
в лог — почту слать некуда, но проверить флоу можно.

Защита: токены сессий хранятся в БД только хешами и живут ограниченное время
(bot/db.py), на вход/регистрацию/коды/сообщения стоят лимиты частоты (bot/ratelimit.py),
блокировка аккаунта сразу обрывает все его сессии.

Чаты двух видов:
- анонимный — подбор собеседника по полу («такой же» / «противоположный» / «любой»),
  по желанию только среди тех, кто идёт на тот же ивент. Очередь и пары держим в памяти
  процесса (WebMatchmaker): они меняются каждую секунду, в БД их хранить не нужно.
  Собеседник видит только ник: ни id, ни имя наружу не отдаются, пока человек сам не
  нажмёт «Раскрыть личность»;
- прямой — по поиску человека по ФИО, постоянный диалог на пару, с непрочитанными.

Сообщения лежат в БД и забираются поллингом (GET /api/chat/poll, /api/chat/direct/poll).
На собеседника можно пожаловаться: жалоба блокирует пару друг для друга, а после жалоб
от нескольких разных людей аккаунт блокируется автоматически.
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

from bot.db import Database
from bot.ratelimit import RateLimiter, client_ip, enforce

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 80
MAX_FIELD_LEN = 120
MAX_NICKNAME_LEN = 40
MAX_MESSAGE_LEN = 2000
MAX_REASON_LEN = 300
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 200
GENDERS = {"male", "female"}
PREFERENCES = {"any", "same", "opposite"}
ACCOUNT_TYPES = {"student", "teacher", "registrar"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DEFAULT_REPORTS_TO_BAN = 3

OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

# Лимиты частоты: (сколько, за сколько секунд)
LOGIN_FAILS = (8, 15 * 60)        # неверных паролей на одну почту
LOGIN_PER_IP = (40, 15 * 60)
REGISTER_PER_IP = (20, 60 * 60)
CODE_COOLDOWN = (1, 45)           # одно письмо с кодом на почту не чаще, чем раз в 45 секунд
CODES_PER_EMAIL = (6, 60 * 60)
CODES_PER_IP = (30, 60 * 60)
VERIFY_PER_IP = (60, 15 * 60)
MESSAGES_PER_USER = (30, 15)
PHOTOS_PER_USER = (10, 60)
REPORTS_PER_USER = (10, 60 * 60)


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return digest, salt


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    digest, _ = _hash_password(password, salt)
    return hmac.compare_digest(digest, expected_hash)


# Для несуществующей почты всё равно считаем хеш — чтобы по времени ответа нельзя было
# понять, зарегистрирован ли адрес.
_DUMMY_HASH, _DUMMY_SALT = _hash_password("dummy-password-for-timing")


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit() or ch == "+")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(EMAIL_RE.fullmatch(email))


def _valid_password(password: str) -> bool:
    return MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _otp_expiry_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS)).isoformat(timespec="seconds")


def _generate_otp() -> str:
    return str(secrets.randbelow(900_000) + 100_000)


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _hash_invite_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def sniff_image_type(data: bytes) -> str | None:
    """Тип картинки по сигнатуре файла — заявленному клиентом content_type не доверяем."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _send_otp_email(app: web.Application, to_email: str, code: str, purpose: str = "verify") -> None:
    api_key = app.get("brevo_api_key") or ""
    sender_email = app.get("brevo_sender_email") or ""
    if not api_key or not sender_email:
        logger.warning("BREVO_API_KEY или BREVO_SENDER_EMAIL не заданы — код для %s: %s (письмо не отправлено)",
                       to_email, code)
        return
    if purpose == "reset":
        subject = "Сброс пароля в Pople"
        lead = "Код для сброса пароля"
        tail = "Если пароль сбрасывали не вы — просто проигнорируйте письмо, пароль останется прежним."
    else:
        subject = "Код подтверждения Pople"
        lead = "Твой код подтверждения"
        tail = "Если это были не вы — просто проигнорируйте письмо."
    payload = {
        "sender": {"name": app.get("brevo_sender_name") or "Pople", "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": (f"<p>{lead}: <b>{code}</b></p>"
                        f"<p>Код действует 10 минут. Никому его не сообщайте. {tail}</p>"),
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
        if a["event_id"] != b["event_id"]:
            return False  # «компания на ивент» ищется только среди тех, кто ищет на тот же ивент
        if a["user_id"] in b["blocked"] or b["user_id"] in a["blocked"]:
            return False
        return (self._wants(a["preference"], a["gender"], b["gender"])
                and self._wants(b["preference"], b["gender"], a["gender"]))

    def search(self, user_id: int, gender: str, preference: str, event_id: str | None = None,
               blocked: frozenset[int] | set[int] = frozenset()) -> dict:
        """Ищет подходящего собеседника в очереди; если не нашёл — встаёт в очередь сам."""
        self.leave(user_id)
        me = {"user_id": user_id, "gender": gender, "preference": preference,
              "event_id": event_id, "blocked": set(blocked)}
        for i, other in enumerate(self._queue):
            if self._compatible(me, other):
                self._queue.pop(i)
                dialog_id = secrets.token_hex(8)
                self._pairs[user_id] = {"partner_id": other["user_id"], "dialog_id": dialog_id, "event_id": event_id}
                self._pairs[other["user_id"]] = {"partner_id": user_id, "dialog_id": dialog_id, "event_id": event_id}
                return {"status": "matched", "dialog_id": dialog_id, "partner_id": other["user_id"],
                        "event_id": event_id}
        self._queue.append(me)
        return {"status": "waiting", "event_id": event_id}

    def status(self, user_id: int) -> dict:
        pair = self._pairs.get(user_id)
        if pair:
            return {"status": "matched", "dialog_id": pair["dialog_id"], "partner_id": pair["partner_id"],
                    "event_id": pair["event_id"]}
        for entry in self._queue:
            if entry["user_id"] == user_id:
                return {"status": "waiting", "event_id": entry["event_id"]}
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

    def waiting_count(self) -> int:
        return len(self._queue)


async def read_json_body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(reason="invalid json")
    return data


def _bearer_token(request: web.Request) -> str:
    return request.headers.get("Authorization", "").removeprefix("Bearer ").strip()


def require_session(request: web.Request) -> int:
    """id веб-пользователя по заголовку Authorization: Bearer <token>.
    Просроченная сессия или заблокированный аккаунт — тоже 401."""
    token = _bearer_token(request)
    if not token:
        raise web.HTTPUnauthorized(reason="missing token")
    db: Database = request.app["db"]
    user_id = db.web_session_user(token)
    if user_id is None:
        raise web.HTTPUnauthorized(reason="invalid session")
    return user_id


def _int_query(request: web.Request, name: str, default: int = 0) -> int:
    raw = request.query.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise web.HTTPBadRequest(reason=f"{name} must be an integer")


def _user_payload(row) -> dict:
    """То, что аккаунт видит о себе самом (не о других)."""
    return {
        "id": row["id"], "first_name": row["first_name"], "last_name": row["last_name"],
        "email": row["email"], "phone": row["phone"], "gender": row["gender"],
        "nickname": row["nickname"], "course": row["course"], "specialty": row["specialty"],
        "role": row["role"],
    }


def _issue_session(db: Database, row) -> web.Response:
    token = secrets.token_urlsafe(32)
    db.create_web_session(token, row["id"])
    return web.json_response({"ok": True, "token": token, "user": _user_payload(row)})


async def _issue_code(request: web.Request, email: str, purpose: str) -> None:
    """Шлёт код на почту с лимитами: не чаще раза в 45 секунд и не больше нескольких в час."""
    enforce(request, f"code-cooldown:{email}", *CODE_COOLDOWN)
    enforce(request, f"codes:{email}", *CODES_PER_EMAIL)
    code = _generate_otp()
    request.app["db"].save_otp_code(email, _hash_otp(code), _otp_expiry_iso())
    await _send_otp_email(request.app, email, code, purpose)


def _check_code(db: Database, email: str, code: str) -> None:
    """Проверяет код из письма; неверная попытка считается, после OTP_MAX_ATTEMPTS код сгорает."""
    otp = db.get_otp_code(email)
    if otp is None or otp["expires_at"] < _now_iso():
        raise web.HTTPUnauthorized(reason="code expired or not requested")
    if otp["attempts"] >= OTP_MAX_ATTEMPTS:
        raise web.HTTPForbidden(reason="too many attempts, request a new code")
    if not code or not hmac.compare_digest(otp["code_hash"], _hash_otp(code)):
        db.bump_otp_attempts(email)
        raise web.HTTPUnauthorized(reason="invalid code")


async def register(request: web.Request) -> web.Response:
    """Создаёт аккаунт (неподтверждённый) и сразу шлёт код на почту — подтвердить
    его нужно один раз через /api/chat/otp/verify, чтобы получить токен и войти.

    account_type различает три сценария регистрации:
    - 'student' (по умолчанию) — открытая регистрация: курс, специальность,
      пол (для подбора в анонимном чате) и анонимный ник обязательны/автогенерируются.
    - 'teacher' / 'registrar' — аккаунт сотрудника (ведёт дисциплины и ставит оценки/
      посещаемость, либо управляет расписанием). Эти роли нельзя получить просто
      регистрацией: нужен invite_key, текущее значение которого знает только админ
      и кому он его передал. Курс/специальность/пол/ник для сотрудников не собираем.

    Почта и телефон используются один раз. Если регистрацию бросили на шаге с кодом,
    её можно закончить позже: вход с паролем предложит прислать код заново, а аккаунты,
    которые так и не подтвердили почту за UNVERIFIED_TTL_DAYS, удаляются автоматически
    (db.purge_expired) — чужую почту или телефон нельзя «занять» навсегда."""
    enforce(request, f"register-ip:{client_ip(request)}", *REGISTER_PER_IP)
    data = await read_json_body(request)
    first_name = str(data.get("first_name", "")).strip()[:MAX_NAME_LEN]
    last_name = str(data.get("last_name", "")).strip()[:MAX_NAME_LEN]
    phone = _normalize_phone(str(data.get("phone", "")))[:20]
    email = _normalize_email(str(data.get("email", "")))
    password = str(data.get("password", ""))
    account_type = str(data.get("account_type", "student")).strip().lower() or "student"

    if account_type not in ACCOUNT_TYPES:
        raise web.HTTPBadRequest(reason="account_type must be student, teacher or registrar")
    if not first_name or not last_name:
        raise web.HTTPBadRequest(reason="first_name and last_name are required")
    if len(phone) < 7:
        raise web.HTTPBadRequest(reason="invalid phone")
    if not _valid_email(email):
        raise web.HTTPBadRequest(reason="invalid email")
    if not _valid_password(password):
        raise web.HTTPBadRequest(reason=f"password must be at least {MIN_PASSWORD_LEN} characters")

    db: Database = request.app["db"]

    if account_type == "student":
        gender = str(data.get("gender", "")).strip().lower()
        course = str(data.get("course", "")).strip()[:MAX_FIELD_LEN]
        specialty = str(data.get("specialty", "")).strip()[:MAX_FIELD_LEN]
        nickname = str(data.get("nickname", "")).strip()[:MAX_NICKNAME_LEN]
        if gender not in GENDERS:
            raise web.HTTPBadRequest(reason="gender must be male or female")
        if not course:
            raise web.HTTPBadRequest(reason="course is required")
        if not specialty:
            raise web.HTTPBadRequest(reason="specialty is required")
        if not nickname:
            nickname = f"Аноним-{secrets.randbelow(9000) + 1000}"
        role = "student"
    else:
        invite_key = str(data.get("invite_key", "")).strip()
        if not invite_key:
            raise web.HTTPBadRequest(reason="invite_key is required")
        expected_hash = db.get_invite_key_hash(account_type)
        if not expected_hash or not hmac.compare_digest(_hash_invite_key(invite_key), expected_hash):
            raise web.HTTPForbidden(reason="invalid or outdated invite key")
        gender, course, specialty, nickname = "", "", "", ""
        role = account_type

    if db.get_web_user_by_phone(phone) is not None or db.get_web_user_by_email(email) is not None:
        raise web.HTTPConflict(reason="phone or email already registered")

    password_hash, salt = _hash_password(password)
    db.create_web_user(first_name, last_name, phone, email, password_hash, salt, gender,
                        course, specialty, nickname, role=role)
    await _issue_code(request, email, "verify")
    return web.json_response({"ok": True, "email": email}, status=201)


async def login(request: web.Request) -> web.Response:
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    password = str(data.get("password", ""))[:MAX_PASSWORD_LEN]

    limiter: RateLimiter = request.app["limiter"]
    enforce(request, f"login-ip:{client_ip(request)}", *LOGIN_PER_IP)
    fail_key = f"login-fail:{email}"
    if limiter.blocked(fail_key, *LOGIN_FAILS):
        raise web.HTTPTooManyRequests(reason="too many failed attempts, try again later")

    db: Database = request.app["db"]
    row = db.get_web_user_by_email(email)
    if row is None:
        _verify_password(password, _DUMMY_SALT, _DUMMY_HASH)
        limiter.fail(fail_key)
        raise web.HTTPUnauthorized(reason="invalid email or password")
    if not _verify_password(password, row["password_salt"], row["password_hash"]):
        limiter.fail(fail_key)
        raise web.HTTPUnauthorized(reason="invalid email or password")
    if row["banned"]:
        raise web.HTTPForbidden(reason="banned")
    if not row["email_verified"]:
        raise web.HTTPForbidden(reason="email not verified")

    limiter.reset(fail_key)
    return _issue_session(db, row)


async def otp_request(request: web.Request) -> web.Response:
    """Повторно шлёт код подтверждения на почту — нужен, если первый код истёк или
    потерялся. Ответ одинаковый для любой почты: по нему нельзя узнать, кто зарегистрирован."""
    enforce(request, f"codes-ip:{client_ip(request)}", *CODES_PER_IP)
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    if not _valid_email(email):
        raise web.HTTPBadRequest(reason="invalid email")

    row = request.app["db"].get_web_user_by_email(email)
    if row is not None and not row["banned"]:
        await _issue_code(request, email, "verify")
    return web.json_response({"ok": True})


async def otp_verify(request: web.Request) -> web.Response:
    """Подтверждает почту кодом из письма и сразу выдаёт токен сессии (как логин)."""
    enforce(request, f"verify-ip:{client_ip(request)}", *VERIFY_PER_IP)
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    code = str(data.get("code", "")).strip()

    db: Database = request.app["db"]
    _check_code(db, email, code)
    row = db.get_web_user_by_email(email)
    if row is None:
        raise web.HTTPNotFound(reason="user not found")
    if row["banned"]:
        raise web.HTTPForbidden(reason="banned")
    db.clear_otp_code(email)
    db.mark_web_email_verified(email)
    return _issue_session(db, db.get_web_user(row["id"]))


async def password_forgot(request: web.Request) -> web.Response:
    """Шаг 1 сброса пароля: код на почту. Ответ одинаковый, есть такой аккаунт или нет."""
    enforce(request, f"codes-ip:{client_ip(request)}", *CODES_PER_IP)
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    if not _valid_email(email):
        raise web.HTTPBadRequest(reason="invalid email")

    row = request.app["db"].get_web_user_by_email(email)
    if row is not None and not row["banned"]:
        await _issue_code(request, email, "reset")
    return web.json_response({"ok": True})


async def password_reset(request: web.Request) -> web.Response:
    """Шаг 2: код из письма + новый пароль. Все прежние сессии сбрасываются, выдаётся новая."""
    enforce(request, f"verify-ip:{client_ip(request)}", *VERIFY_PER_IP)
    data = await read_json_body(request)
    email = _normalize_email(str(data.get("email", "")))
    code = str(data.get("code", "")).strip()
    password = str(data.get("password", ""))
    if not _valid_password(password):
        raise web.HTTPBadRequest(reason=f"password must be at least {MIN_PASSWORD_LEN} characters")

    db: Database = request.app["db"]
    _check_code(db, email, code)
    row = db.get_web_user_by_email(email)
    if row is None:
        raise web.HTTPNotFound(reason="user not found")
    if row["banned"]:
        raise web.HTTPForbidden(reason="banned")
    db.clear_otp_code(email)
    password_hash, salt = _hash_password(password)
    db.set_web_password(row["id"], password_hash, salt)
    db.mark_web_email_verified(email)  # код пришёл на почту — значит, почта его
    db.delete_web_sessions_for_user(row["id"])
    request.app["limiter"].reset(f"login-fail:{email}")
    return _issue_session(db, db.get_web_user(row["id"]))


async def logout(request: web.Request) -> web.Response:
    user_id = require_session(request)
    request.app["db"].delete_web_session(_bearer_token(request))
    request.app["web_mm"].leave(user_id)
    return web.json_response({"ok": True})


async def logout_all(request: web.Request) -> web.Response:
    """Выйти на всех устройствах, кроме текущего."""
    user_id = require_session(request)
    request.app["db"].delete_web_sessions_for_user(user_id, except_token=_bearer_token(request))
    return web.json_response({"ok": True})


async def me(request: web.Request) -> web.Response:
    user_id = require_session(request)
    row = request.app["db"].get_web_user(user_id)
    if row is None:
        raise web.HTTPUnauthorized(reason="user not found")
    return web.json_response(_user_payload(row))


async def update_profile(request: web.Request) -> web.Response:
    """Меняет имя, фамилию, анонимный ник, курс и специальность. Почту, телефон и роль — нельзя."""
    user_id = require_session(request)
    data = await read_json_body(request)
    limits = {"first_name": MAX_NAME_LEN, "last_name": MAX_NAME_LEN, "nickname": MAX_NICKNAME_LEN,
              "course": MAX_FIELD_LEN, "specialty": MAX_FIELD_LEN}
    fields = {key: str(data[key]).strip()[:limit] for key, limit in limits.items() if key in data}
    for required in ("first_name", "last_name"):
        if required in fields and not fields[required]:
            raise web.HTTPBadRequest(reason=f"{required} can't be empty")

    db: Database = request.app["db"]
    db.update_web_profile(user_id, fields)
    return web.json_response({"ok": True, "user": _user_payload(db.get_web_user(user_id))})


async def password_change(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    current = str(data.get("current_password", ""))[:MAX_PASSWORD_LEN]
    new = str(data.get("new_password", ""))
    if not _valid_password(new):
        raise web.HTTPBadRequest(reason=f"password must be at least {MIN_PASSWORD_LEN} characters")

    enforce(request, f"password-change:{user_id}", 10, 15 * 60)
    db: Database = request.app["db"]
    row = db.get_web_user(user_id)
    if not _verify_password(current, row["password_salt"], row["password_hash"]):
        raise web.HTTPForbidden(reason="current password is wrong")
    password_hash, salt = _hash_password(new)
    db.set_web_password(user_id, password_hash, salt)
    db.delete_web_sessions_for_user(user_id, except_token=_bearer_token(request))
    return web.json_response({"ok": True})


async def account_delete(request: web.Request) -> web.Response:
    """Необратимо удаляет аккаунт со всеми данными. Нужен текущий пароль."""
    user_id = require_session(request)
    data = await read_json_body(request)
    enforce(request, f"account-delete:{user_id}", 10, 15 * 60)
    db: Database = request.app["db"]
    row = db.get_web_user(user_id)
    if not _verify_password(str(data.get("password", ""))[:MAX_PASSWORD_LEN], row["password_salt"], row["password_hash"]):
        raise web.HTTPForbidden(reason="password is wrong")
    if row["role"] == "admin" and db.count_web_admins() <= 1:
        raise web.HTTPConflict(reason="the last admin can't delete their account")
    request.app["web_mm"].leave(user_id)
    db.delete_web_account(user_id)
    return web.json_response({"ok": True})


# ---------- анонимный чат ----------

def _partner_nickname(db: Database, partner_id: int) -> str:
    row = db.get_web_user(partner_id)
    return (row["nickname"] if row is not None else "") or "Собеседник"


def _serialize_message(m, user_id: int) -> dict:
    out = {"id": m["id"], "mine": m["sender_id"] == user_id, "kind": m["kind"], "at": m["created_at"]}
    if m["kind"] == "photo":
        out["photo_id"] = m["photo_id"]
    else:
        out["text"] = m["text"]
    return out


def _event_title(request: web.Request, event_id: str | None) -> str | None:
    if not event_id:
        return None
    event = request.app["content"].event(event_id) if request.app.get("content") else None
    return event.title if event else None


def _public_state(request: web.Request, state: dict) -> dict:
    """Состояние подбора для клиента. partner_id наружу не отдаём: по нему собеседника
    можно было бы найти в разделе «Люди» и узнать его имя — это ломало бы анонимность."""
    out = {key: value for key, value in state.items() if key != "partner_id"}
    if state.get("event_id"):
        out["event_title"] = _event_title(request, state["event_id"])
    if state["status"] == "matched":
        out["partner_nickname"] = _partner_nickname(request.app["db"], state["partner_id"])
    return out


def _start_search(request: web.Request, user_id: int, preference: str, event_id: str | None = None) -> dict:
    if preference not in PREFERENCES:
        raise web.HTTPBadRequest(reason="preference must be any, same or opposite")
    db: Database = request.app["db"]
    row = db.get_web_user(user_id)
    if row["role"] not in ("student", "admin"):
        raise web.HTTPForbidden(reason="anonymous chat is for student or admin accounts only")
    if event_id is not None and _event_title(request, event_id) is None:
        raise web.HTTPNotFound(reason="event not found")
    mm: WebMatchmaker = request.app["web_mm"]
    result = mm.search(user_id, row["gender"], preference, event_id, db.blocked_either_way(user_id))
    if result["status"] == "matched":
        db.create_web_dialog(result["dialog_id"], user_id, result["partner_id"], event_id=event_id)
    return _public_state(request, result)


def _event_id_from(data: dict) -> str | None:
    return str(data.get("event_id") or "").strip()[:MAX_FIELD_LEN] or None


async def search(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    preference = str(data.get("preference", "any")).strip().lower()
    return web.json_response(_start_search(request, user_id, preference, _event_id_from(data)))


async def poll(request: web.Request) -> web.Response:
    user_id = require_session(request)
    mm: WebMatchmaker = request.app["web_mm"]
    state = mm.status(user_id)
    out = _public_state(request, state)
    if state["status"] == "matched":
        messages = request.app["db"].web_messages_since(state["dialog_id"], _int_query(request, "since"))
        out["messages"] = [_serialize_message(m, user_id) for m in messages]
    return web.json_response(out)


def _require_current_dialog(request: web.Request, user_id: int, dialog_id: str) -> dict:
    state = request.app["web_mm"].status(user_id)
    if state["status"] != "matched" or state["dialog_id"] != dialog_id:
        raise web.HTTPConflict(reason="not in this dialog")
    return state


def _message_text(data: dict) -> str:
    text = str(data.get("text", "")).strip()[:MAX_MESSAGE_LEN]
    if not text:
        raise web.HTTPBadRequest(reason="text is required")
    return text


def _decode_photo(data: dict) -> tuple[str, bytes]:
    """base64-фото из тела запроса → (настоящий тип по сигнатуре, байты)."""
    try:
        photo_bytes = base64.b64decode(str(data.get("data", "")), validate=True)
    except Exception:
        raise web.HTTPBadRequest(reason="invalid photo data")
    if not photo_bytes or len(photo_bytes) > MAX_PHOTO_BYTES:
        raise web.HTTPBadRequest(reason="photo too large or empty")
    content_type = sniff_image_type(photo_bytes)
    if content_type is None:
        raise web.HTTPBadRequest(reason="unsupported photo type")
    return content_type, photo_bytes


async def send(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    text = _message_text(data)
    _require_current_dialog(request, user_id, dialog_id)
    enforce(request, f"msg:{user_id}", *MESSAGES_PER_USER)
    msg_id = request.app["db"].add_web_message(dialog_id, user_id, text)
    return web.json_response({"ok": True, "id": msg_id})


async def send_photo(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    content_type, photo_bytes = _decode_photo(data)
    _require_current_dialog(request, user_id, dialog_id)
    enforce(request, f"photo:{user_id}", *PHOTOS_PER_USER)

    db: Database = request.app["db"]
    photo_id = db.add_web_photo(dialog_id, user_id, content_type, photo_bytes)
    msg_id = db.add_web_message(dialog_id, user_id, "", kind="photo", photo_id=photo_id)
    return web.json_response({"ok": True, "id": msg_id, "photo_id": photo_id})


async def get_photo(request: web.Request) -> web.Response:
    user_id = require_session(request)
    try:
        photo_id = int(request.match_info["photo_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid photo id")

    db: Database = request.app["db"]
    photo = db.get_web_photo(photo_id)
    if photo is None:
        raise web.HTTPNotFound(reason="photo not found")
    dialog = db.get_web_dialog(photo["dialog_id"])
    if dialog is None or user_id not in (dialog["user_a"], dialog["user_b"]):
        raise web.HTTPForbidden(reason="not a participant of this dialog")

    return web.Response(body=photo["data"], content_type=photo["content_type"],
                        headers={"Cache-Control": "private, max-age=86400"})


async def reveal(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    _require_current_dialog(request, user_id, dialog_id)

    db: Database = request.app["db"]
    row = db.get_web_user(user_id)
    full_name = f"{row['first_name']} {row['last_name']}".strip()
    msg_id = db.add_web_message(dialog_id, user_id, full_name, kind="reveal")
    return web.json_response({"ok": True, "id": msg_id})


async def _leave(request: web.Request, requeue: bool) -> web.Response:
    user_id = require_session(request)
    request.app["web_mm"].leave(user_id)
    if not requeue:
        return web.json_response({"status": "idle"})

    preference, event_id = "any", None
    if request.can_read_body:
        try:
            data = await request.json()
            if isinstance(data, dict):
                preference = str(data.get("preference", "any")).strip().lower()
                event_id = _event_id_from(data)
        except ValueError:
            pass
    if preference not in PREFERENCES:
        preference = "any"
    return web.json_response(_start_search(request, user_id, preference, event_id))


async def chat_next(request: web.Request) -> web.Response:
    return await _leave(request, requeue=True)


async def chat_stop(request: web.Request) -> web.Response:
    return await _leave(request, requeue=False)


# ---------- жалобы и блокировки ----------

async def report(request: web.Request) -> web.Response:
    """Жалоба на собеседника — в анонимном чате (текущий диалог) или в прямом.
    Пара сразу блокируется друг для друга, анонимный диалог завершается. Когда на одного
    человека пожаловались REPORTS_TO_BAN разных людей — его аккаунт блокируется автоматически."""
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    reason = str(data.get("reason", "")).strip()[:MAX_REASON_LEN]
    enforce(request, f"report:{user_id}", *REPORTS_PER_USER)

    db: Database = request.app["db"]
    mm: WebMatchmaker = request.app["web_mm"]
    dialog = db.get_web_dialog(dialog_id)
    if dialog is None or user_id not in (dialog["user_a"], dialog["user_b"]):
        raise web.HTTPForbidden(reason="not a participant of this dialog")
    target_id = _other_participant(dialog, user_id)

    reporters = db.add_web_report(user_id, target_id, dialog_id, reason)
    db.add_web_block(user_id, target_id)
    if dialog["mode"] == "anonymous" and mm.status(user_id).get("dialog_id") == dialog_id:
        mm.leave(user_id)

    banned = False
    target = db.get_web_user(target_id)
    if (reporters >= request.app["reports_to_ban"] and target is not None
            and not target["banned"] and target["role"] != "admin"):
        db.set_web_user_banned(target_id, True)
        mm.leave(target_id)
        banned = True
        logger.warning("Аккаунт %s заблокирован автоматически: жалобы от %s разных людей", target_id, reporters)
    return web.json_response({"ok": True, "banned": banned})


async def block_user(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="user_id is required")
    if target_id == user_id:
        raise web.HTTPBadRequest(reason="can't block yourself")
    db: Database = request.app["db"]
    if bool(data.get("blocked", True)):
        if db.get_web_user(target_id) is None:
            raise web.HTTPNotFound(reason="user not found")
        db.add_web_block(user_id, target_id)
    else:
        db.remove_web_block(user_id, target_id)
    return web.json_response({"ok": True})


async def list_blocks(request: web.Request) -> web.Response:
    user_id = require_session(request)
    rows = request.app["db"].blocks_of(user_id)
    return web.json_response({"blocked": [
        {"id": r["id"], "first_name": r["first_name"], "last_name": r["last_name"]} for r in rows]})


# ---------- Прямой чат по ФИО (не анонимный): ищешь конкретного человека и пишешь ему ----------
# Анонимный чат выше — это одна из фич сайта, а не единственный способ пообщаться. Здесь —
# обычный поиск по имени/фамилии среди зарегистрированных и постоянный (не одноразовый,
# как в подборе по полу) диалог с конкретным человеком, по одному диалогу на пару.

SEARCH_LIMIT = 100
ROLE_LABELS = {"teacher": "Преподаватель", "registrar": "Отдел расписания", "admin": "Администратор"}


def _public_user(row) -> dict:
    """Что о человеке видят другие в разделе «Люди». Анонимного ника здесь нет намеренно:
    рядом с настоящим именем он раскрыл бы, кто скрывается под ником в анонимном чате."""
    return {
        "id": row["id"], "first_name": row["first_name"], "last_name": row["last_name"],
        "course": row["course"], "specialty": row["specialty"],
        "role_label": ROLE_LABELS.get(row["role"], ""),
    }


async def search_users(request: web.Request) -> web.Response:
    """Пустой q — весь список зарегистрированных (как справочник), непустой — фильтр по ФИО."""
    user_id = require_session(request)
    query = request.query.get("q", "").strip()[:MAX_NAME_LEN]
    db: Database = request.app["db"]
    blocked = db.blocked_either_way(user_id)
    rows = [r for r in db.search_web_users(query, exclude_id=user_id, limit=SEARCH_LIMIT)
            if r["id"] not in blocked and r["email_verified"]]
    return web.json_response({"users": [_public_user(r) for r in rows]})


async def direct_start(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    try:
        target_id = int(data.get("user_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="user_id is required")
    if target_id == user_id:
        raise web.HTTPBadRequest(reason="can't start a dialog with yourself")

    db: Database = request.app["db"]
    target = db.get_web_user(target_id)
    if target is None or target["banned"]:
        raise web.HTTPNotFound(reason="user not found")
    if target_id in db.blocked_either_way(user_id):
        raise web.HTTPForbidden(reason="blocked")

    dialog = db.find_direct_dialog(user_id, target_id)
    if dialog is None:
        dialog_id = secrets.token_hex(8)
        db.create_web_dialog(dialog_id, user_id, target_id, mode="direct")
    else:
        dialog_id = dialog["dialog_id"]
    return web.json_response({"ok": True, "dialog_id": dialog_id, "partner": _public_user(target)})


def _other_participant(dialog, user_id: int) -> int:
    return dialog["user_b"] if dialog["user_a"] == user_id else dialog["user_a"]


def _message_preview(m) -> str:
    if m is None:
        return ""
    if m["kind"] == "photo":
        return "📷 Фото"
    return m["text"][:80]


async def direct_list(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    blocked = db.blocked_either_way(user_id)
    out = []
    for item in db.direct_dialogs_overview(user_id):
        partner = db.get_web_user(item["partner_id"])
        if partner is None:
            continue
        last = item["last"]
        out.append({
            "dialog_id": item["dialog_id"], "partner": _public_user(partner),
            "unread": item["unread"], "last_at": item["last_at"],
            "last_text": _message_preview(last), "last_mine": bool(last and last["sender_id"] == user_id),
            "blocked": partner["id"] in blocked,
        })
    return web.json_response({"dialogs": out})


async def unread(request: web.Request) -> web.Response:
    """Сколько непрочитанных сообщений в прямых чатах — для значка в меню."""
    user_id = require_session(request)
    return web.json_response({"unread": request.app["db"].unread_direct_count(user_id)})


def _require_direct_dialog(db: Database, dialog_id: str, user_id: int):
    dialog = db.get_web_dialog(dialog_id)
    if dialog is None or dialog["mode"] != "direct" or user_id not in (dialog["user_a"], dialog["user_b"]):
        raise web.HTTPForbidden(reason="not a participant of this dialog")
    return dialog


def _require_writable_direct_dialog(db: Database, dialog_id: str, user_id: int):
    dialog = _require_direct_dialog(db, dialog_id, user_id)
    if _other_participant(dialog, user_id) in db.blocked_either_way(user_id):
        raise web.HTTPForbidden(reason="blocked")
    return dialog


async def direct_poll(request: web.Request) -> web.Response:
    """Новые сообщения диалога; заодно помечает их прочитанными."""
    user_id = require_session(request)
    dialog_id = request.query.get("dialog_id", "").strip()
    db: Database = request.app["db"]
    _require_direct_dialog(db, dialog_id, user_id)

    messages = db.web_messages_since(dialog_id, _int_query(request, "since"))
    if messages:
        db.mark_dialog_read(dialog_id, user_id, messages[-1]["id"])
    return web.json_response({"messages": [_serialize_message(m, user_id) for m in messages]})


async def direct_send(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    text = _message_text(data)

    db: Database = request.app["db"]
    _require_writable_direct_dialog(db, dialog_id, user_id)
    enforce(request, f"msg:{user_id}", *MESSAGES_PER_USER)
    msg_id = db.add_web_message(dialog_id, user_id, text)
    return web.json_response({"ok": True, "id": msg_id})


async def direct_send_photo(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    dialog_id = str(data.get("dialog_id", "")).strip()
    content_type, photo_bytes = _decode_photo(data)

    db: Database = request.app["db"]
    _require_writable_direct_dialog(db, dialog_id, user_id)
    enforce(request, f"photo:{user_id}", *PHOTOS_PER_USER)
    photo_id = db.add_web_photo(dialog_id, user_id, content_type, photo_bytes)
    msg_id = db.add_web_message(dialog_id, user_id, "", kind="photo", photo_id=photo_id)
    return web.json_response({"ok": True, "id": msg_id, "photo_id": photo_id})


def register_routes(app: web.Application, brevo_api_key: str = "", brevo_sender_email: str = "",
                     brevo_sender_name: str = "Pople", reports_to_ban: int = DEFAULT_REPORTS_TO_BAN) -> None:
    app["web_mm"] = WebMatchmaker()
    app.setdefault("limiter", RateLimiter())
    app["reports_to_ban"] = max(1, reports_to_ban)
    app["brevo_api_key"] = brevo_api_key
    app["brevo_sender_email"] = brevo_sender_email
    app["brevo_sender_name"] = brevo_sender_name
    app.router.add_post("/api/chat/register", register)
    app.router.add_post("/api/chat/login", login)
    app.router.add_post("/api/chat/otp/request", otp_request)
    app.router.add_post("/api/chat/otp/verify", otp_verify)
    app.router.add_post("/api/chat/password/forgot", password_forgot)
    app.router.add_post("/api/chat/password/reset", password_reset)
    app.router.add_post("/api/chat/password/change", password_change)
    app.router.add_post("/api/chat/logout", logout)
    app.router.add_post("/api/chat/logout-all", logout_all)
    app.router.add_get("/api/chat/me", me)
    app.router.add_post("/api/chat/profile", update_profile)
    app.router.add_post("/api/chat/account/delete", account_delete)
    app.router.add_post("/api/chat/search", search)
    app.router.add_get("/api/chat/poll", poll)
    app.router.add_post("/api/chat/send", send)
    app.router.add_post("/api/chat/send-photo", send_photo)
    app.router.add_get("/api/chat/photo/{photo_id}", get_photo)
    app.router.add_post("/api/chat/reveal", reveal)
    app.router.add_post("/api/chat/next", chat_next)
    app.router.add_post("/api/chat/stop", chat_stop)
    app.router.add_post("/api/chat/report", report)
    app.router.add_post("/api/chat/block", block_user)
    app.router.add_get("/api/chat/blocks", list_blocks)
    app.router.add_get("/api/chat/unread", unread)
    app.router.add_get("/api/chat/direct/search-users", search_users)
    app.router.add_post("/api/chat/direct/start", direct_start)
    app.router.add_get("/api/chat/direct/list", direct_list)
    app.router.add_get("/api/chat/direct/poll", direct_poll)
    app.router.add_post("/api/chat/direct/send", direct_send)
    app.router.add_post("/api/chat/direct/send-photo", direct_send_photo)
