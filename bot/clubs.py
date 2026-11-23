"""Клубы: подписки студентов, уведомления и кабинет организатора.

События клуба хранятся там же, где походы (bot/hikes.py, таблица hikes с полем kind):
запись, лимит мест и лист ожидания уже работали для Summit Seekers — кабинет даёт
организатору любого клуба управлять ими самому. Организатор видит и меняет только
свой клуб (web_users.club_id); админ может открыть кабинет любого клуба: ?club=<id>.
"""
import asyncio
import csv
import html
import io
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web

from bot.content import Content
from bot.db import Database
from bot.hikes import DIFFICULTIES, EVENT_KINDS, now as hikes_now
from bot.web_chat import read_json_body, require_session

logger = logging.getLogger(__name__)

MAX_TEXT = 300
REMIND_BEFORE = timedelta(hours=24)
JOBS_INTERVAL_SECONDS = 60
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
# Куда ведут ссылки в письмах. Свой адрес сайта — переменная SITE_URL.
SITE_URL = os.getenv("SITE_URL", "https://almau-messenger.vercel.app").rstrip("/")
_SRC_RE = re.compile(r"[^a-z0-9_-]")

# Формы партнёров на сайте (bot/api.py, POST /api/leads) пишут заявки с этими event_id.
# По ним организатор видит в кабинете заявки своего клуба.
CLUB_LEAD_EVENTS = {
    "remus": ("dtmm",), "hunters": ("open-day",), "mafia": ("mafia-friday",), "mof": ("mof-date",),
}
LEAD_STATUSES = {"new": "новая", "contacted": "связались", "paid": "оплачено", "cancelled": "отменена"}


def clean_src(raw: str) -> str:
    """Метка источника из ссылки (?src=ig): только латиница, цифры, - и _, до 20 символов."""
    return _SRC_RE.sub("", str(raw or "").lower())[:20]


def hike_key(web_user_id: int) -> int:
    """В записях на события аккаунты сайта лежат под отрицательным id (исторически плюс — Telegram)."""
    return -web_user_id


def _stamp() -> str:
    return hikes_now().isoformat(timespec="seconds")


# ---------- уведомления ----------

def _email_html(text: str, link: str, buttons: list[tuple[str, str]] | None = None) -> str:
    """Простое письмо: текст и кнопки. Ссылки ведут на сайт."""
    btns = buttons or ([("Открыть в Pople", SITE_URL + "/" + link)] if link else [])
    btn_html = "".join(
        f'<a href="{html.escape(url)}" style="display:inline-block;margin:6px 8px 0 0;padding:12px 20px;'
        f'background:#a78bfa;color:#0b0b0f;border-radius:999px;text-decoration:none;font-weight:700">'
        f'{html.escape(label)}</a>' for label, url in btns)
    return (f'<div style="font-family:Arial,sans-serif;font-size:16px;line-height:1.5;color:#111">'
            f'<p>{html.escape(text)}</p><p>{btn_html}</p>'
            f'<p style="color:#777;font-size:13px">Pople — студенческая платформа AlmaU. '
            f'Письмо пришло, потому что вы записались на событие клуба.</p></div>')


def queue_email(db: Database, web_user_id: int, subject: str, text: str, link: str = "",
                buttons: list[tuple[str, str]] | None = None) -> bool:
    """Ставит письмо в очередь; отправляет фоновая задача (send_outbox). Демо-почты пропускаем."""
    row = db.get_web_user(web_user_id)
    if row is None or not row["email"] or row["email"].endswith("@example.com"):
        return False
    with db.conn:
        db.conn.execute("INSERT INTO email_outbox (to_email, subject, html, created_at) VALUES (?, ?, ?, ?)",
                        (row["email"], subject[:150], _email_html(text, link, buttons), _stamp()))
    return True


def notify(db: Database, web_user_id: int, text: str, link: str = "", email_subject: str = "",
           buttons: list[tuple[str, str]] | None = None) -> None:
    """Уведомление в колокольчик; с email_subject — ещё и письмо на почту."""
    if web_user_id <= 0:
        return
    with db.conn:
        db.conn.execute("INSERT INTO notifications (user_id, text, link, created_at) VALUES (?, ?, ?, ?)",
                        (web_user_id, text[:MAX_TEXT], link[:120], _stamp()))
    if email_subject:
        queue_email(db, web_user_id, email_subject, text, link, buttons)


def notify_signups(db: Database, hike_id: str, text: str, link: str = "", email_subject: str = "") -> int:
    """Сообщение всем, кто записан на событие (и идущим, и из листа ожидания)."""
    signups = db.hikes.signups(hike_id)
    for s in signups:
        notify(db, -s.user_id, text, link, email_subject)
    return len(signups)


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря")


def when(hike) -> str:
    """«2026-12-12T19:00» → «12 декабря в 19:00» — так дата читается в уведомлениях."""
    d = datetime.fromisoformat(hike.start_at)
    return f"{d.day} {_MONTHS[d.month - 1]} в {d:%H:%M}"


def event_link(hike) -> str:
    """Своя страница у каждого события — ею клуб делится в Instagram и Telegram."""
    return f"#event/{hike.id}"


def promoted_notice(db: Database, hike, signup) -> None:
    """Человека подняли из листа ожидания — сообщаем на сайте и письмом."""
    notify(db, -signup.user_id, f"Освободилось место: вы записаны на «{hike.title}» — {when(hike)}.",
           event_link(hike), email_subject=f"Место освободилось: {hike.title}")


def run_reminders(db: Database) -> int:
    """За сутки до события: напоминание и просьба подтвердить «приду / не смогу».
    Ответ «не смогу» освобождает место для листа ожидания — так клуб меньше теряет на неявке."""
    at = hikes_now()
    sent = 0
    for row in db.conn.execute(
            "SELECT hike_id, user_id FROM hike_signups WHERE status = 'going' AND reminded_24h = 0 AND user_id < 0"
    ).fetchall():
        hike = db.hikes.get(row["hike_id"])
        if hike is None or not hike.is_open or not (at <= hike.start <= at + REMIND_BEFORE):
            continue
        token = secrets.token_urlsafe(16)
        with db.conn:
            db.conn.execute("UPDATE hike_signups SET confirm_token = ?, reminded_24h = 1 WHERE hike_id = ? AND user_id = ?",
                            (token, hike.id, row["user_id"]))
        rsvp = f"{SITE_URL}/#rsvp/{token}"
        notify(db, -row["user_id"],
               f"Напоминание: «{hike.title}» — {when(hike)}, место: {hike.meeting_point or 'уточняется'}. "
               f"Подтвердите, что придёте, или освободите место для других.", event_link(hike),
               email_subject=f"Завтра: {hike.title} — подтвердите, что придёте",
               buttons=[("✅ Приду", rsvp + "/yes"), ("Не смогу", rsvp + "/no")])
        sent += 1
    return sent


def answer_rsvp(db: Database, hike, user_key: int, answer: str) -> str:
    """'yes' — подтвердил; 'no' — запись отменяется, место уходит первому из листа ожидания."""
    if answer == "yes":
        with db.conn:
            db.conn.execute("UPDATE hike_signups SET confirmed = 'yes' WHERE hike_id = ? AND user_id = ?",
                            (hike.id, user_key))
        return "yes"
    _, promoted = db.hikes.cancel_signup(hike.id, user_key)
    if promoted is not None:
        promoted_notice(db, hike, promoted)
    return "no"


async def post_rsvp(request: web.Request) -> web.Response:
    """Ответ по ссылке из письма — без входа на сайт: доступ даёт одноразовый токен."""
    data = await read_json_body(request)
    token = str(data.get("token", ""))[:64]
    answer = str(data.get("answer", ""))
    if answer not in ("yes", "no") or not token:
        raise web.HTTPBadRequest(reason="token and answer (yes|no) are required")
    db: Database = request.app["db"]
    row = db.conn.execute("SELECT hike_id, user_id FROM hike_signups WHERE confirm_token = ?", (token,)).fetchone()
    if row is None:
        raise web.HTTPNotFound(reason="link is no longer valid")
    hike = db.hikes.get(row["hike_id"])
    result = answer_rsvp(db, hike, row["user_id"], answer)
    return web.json_response({"ok": True, "answer": result, "title": hike.title, "when": when(hike),
                              "event_id": hike.id})


async def post_confirm(request: web.Request) -> web.Response:
    """То же подтверждение, но с сайта, под своим аккаунтом."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    hike = db.hikes.get(request.match_info["hike_id"])
    if hike is None or db.hikes.signup_of(hike.id, hike_key(user_id)) is None:
        raise web.HTTPNotFound(reason="signup not found")
    answer = str((await read_json_body(request)).get("answer", ""))
    if answer not in ("yes", "no"):
        raise web.HTTPBadRequest(reason="answer must be yes or no")
    return web.json_response({"ok": True, "answer": answer_rsvp(db, hike, hike_key(user_id), answer)})


async def send_outbox(app: web.Application, limit: int = 20) -> int:
    """Отправляет письма из очереди через Brevo. Без ключей Brevo письма помечаются как пропущенные."""
    db: Database = app["db"]
    rows = db.conn.execute("SELECT * FROM email_outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()
    if not rows:
        return 0
    api_key, sender = app.get("brevo_api_key") or "", app.get("brevo_sender_email") or ""
    sent = 0
    for r in rows:
        if not api_key or not sender:
            result = "skipped: Brevo не настроен"
        else:
            payload = {"sender": {"name": app.get("brevo_sender_name") or "Pople", "email": sender},
                       "to": [{"email": r["to_email"]}], "subject": r["subject"], "htmlContent": r["html"]}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(BREVO_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=10),
                                            headers={"api-key": api_key, "accept": "application/json"}) as resp:
                        result = "sent" if resp.status < 300 else f"error: {resp.status} {(await resp.text())[:150]}"
            except Exception as exc:   # сеть или Brevo недоступны — письмо не теряем, пробуем позже
                logger.warning("Письмо не ушло, повторим позже: %s", exc)
                continue
        with db.conn:
            db.conn.execute("UPDATE email_outbox SET sent_at = ?, result = ? WHERE id = ?", (_stamp(), result, r["id"]))
        sent += result == "sent"
    return sent


async def _jobs_loop(app: web.Application) -> None:
    """Раз в минуту: напоминания за сутки и отправка писем из очереди."""
    while True:
        try:
            run_reminders(app["db"])
            await send_outbox(app)
        except Exception:
            logger.exception("Ошибка фоновой задачи клубов")
        await asyncio.sleep(JOBS_INTERVAL_SECONDS)


async def _start_jobs(app: web.Application) -> None:
    app["club_jobs"] = asyncio.create_task(_jobs_loop(app))


async def _stop_jobs(app: web.Application) -> None:
    task = app.get("club_jobs")
    if task:
        task.cancel()


async def get_notifications(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    rows = db.conn.execute(
        "SELECT id, text, link, created_at, read_at FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 30",
        (user_id,)).fetchall()
    return web.json_response({
        "unread": sum(1 for r in rows if r["read_at"] is None),
        "items": [{"id": r["id"], "text": r["text"], "link": r["link"], "at": r["created_at"],
                   "unread": r["read_at"] is None} for r in rows],
    })


async def read_notifications(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    with db.conn:
        db.conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                        (_stamp(), user_id))
    return web.json_response({"ok": True})


# ---------- подписки ----------

def followed_clubs(db: Database, user_id: int) -> list[str]:
    return [r["club_id"] for r in db.conn.execute(
        "SELECT club_id FROM club_follows WHERE user_id = ? ORDER BY created_at", (user_id,))]


def followers_count(db: Database, club_id: str) -> int:
    return db.conn.execute("SELECT COUNT(*) FROM club_follows WHERE club_id = ?", (club_id,)).fetchone()[0]


def set_follow(db: Database, user_id: int, club_id: str, follow: bool) -> None:
    with db.conn:
        if follow:
            db.conn.execute("INSERT OR IGNORE INTO club_follows (user_id, club_id, created_at) VALUES (?, ?, ?)",
                            (user_id, club_id, _stamp()))
        else:
            db.conn.execute("DELETE FROM club_follows WHERE user_id = ? AND club_id = ?", (user_id, club_id))


def _club(content: Content, club_id: str):
    for c in content.clubs:
        if c.id == club_id:
            return c
    raise web.HTTPNotFound(reason="club not found")


async def post_follow(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    club = _club(request.app["content"], request.match_info["club_id"])
    data = await read_json_body(request)
    set_follow(db, user_id, club.id, bool(data.get("follow", True)))
    return web.json_response({"ok": True, "following": followed_clubs(db, user_id)})


async def get_my_clubs(request: web.Request) -> web.Response:
    """Мои клубы и их ближайшие события с моим статусом записи — блок на главной."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    content: Content = request.app["content"]
    following = followed_clubs(db, user_id)
    names = {c.id: c.name for c in content.clubs}
    feed = []
    for club_id in following:
        for h in db.hikes.upcoming(club_id):
            going, waitlist = db.hikes.counts(h.id)
            mine = db.hikes.signup_of(h.id, hike_key(user_id))
            feed.append({**h.public_dict(going, waitlist), "club_name": names.get(club_id, club_id),
                         "my_status": mine.status if mine else None,
                         "waitlist_position": db.hikes.waitlist_position(h.id, hike_key(user_id)) if mine else None})
    feed.sort(key=lambda e: e["start"])
    return web.json_response({"following": [{"id": c, "name": names.get(c, c)} for c in following], "feed": feed})


# ---------- кабинет организатора ----------

def _organizer_club(request: web.Request) -> tuple[dict, str]:
    """Кто вошёл и каким клубом управляет. Организатор — только своим, админ — любым (?club=)."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    user = db.get_web_user(user_id)
    if user["role"] == "organizer" and user["club_id"]:
        return user, user["club_id"]
    if user["role"] == "admin":
        club_id = request.query.get("club") or "summit"
        _club(request.app["content"], club_id)
        return user, club_id
    raise web.HTTPForbidden(reason="organizer role required")


def _own_event(request: web.Request, club_id: str):
    hike = request.app["db"].hikes.get(request.match_info["event_id"])
    if hike is None or hike.club_id != club_id:
        raise web.HTTPNotFound(reason="event not found")
    return hike


def _event_row(db: Database, h) -> dict:
    going, waitlist = db.hikes.counts(h.id)
    attended = db.hikes.attended_count(h.id)
    sources = {r["src"] or "сайт": r["n"] for r in db.conn.execute(
        "SELECT src, COUNT(*) AS n FROM hike_signups WHERE hike_id = ? GROUP BY src", (h.id,))}
    confirmed = db.conn.execute("SELECT COUNT(*) FROM hike_signups WHERE hike_id = ? AND confirmed = 'yes'",
                                (h.id,)).fetchone()[0]
    return {**h.public_dict(going, waitlist), "attended": attended, "past": h.end < hikes_now(),
            "turnout": round(attended * 100 / going) if going and attended else None,
            "sources": sources, "confirmed": confirmed, "link": f"{SITE_URL}/?src=share{event_link(h)}"}


def _parse_event(data: dict, partial: bool = False) -> dict:
    """Проверяет поля события из формы организатора. partial — при изменении (поля необязательны)."""
    out = {}

    def text(key: str, required: bool = False, limit: int = MAX_TEXT) -> None:
        if key in data:
            value = str(data[key]).strip()[:limit]
            if required and not value:
                raise web.HTTPBadRequest(reason=f"{key} is required")
            out[key] = value
        elif required and not partial:
            raise web.HTTPBadRequest(reason=f"{key} is required")

    text("title", required=True, limit=120)
    text("meeting_point")
    text("route", limit=600)
    text("price", limit=60)
    if "start_at" in data or not partial:
        raw = str(data.get("start_at", "")).strip()[:16]
        try:
            out["start_at"] = datetime.fromisoformat(raw).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            raise web.HTTPBadRequest(reason="start_at must look like 2026-12-05T18:30")
    if "capacity" in data or not partial:
        try:
            out["capacity"] = int(data.get("capacity", 20))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(reason="capacity must be a number")
        if not 1 <= out["capacity"] <= 1000:
            raise web.HTTPBadRequest(reason="capacity must be between 1 and 1000")
    if "duration_hours" in data:
        try:
            out["duration_hours"] = float(data["duration_hours"])
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(reason="duration_hours must be a number")
        if not 0.5 <= out["duration_hours"] <= 72:
            raise web.HTTPBadRequest(reason="duration_hours must be between 0.5 and 72")
    if "kind" in data:
        if data["kind"] not in EVENT_KINDS:
            raise web.HTTPBadRequest(reason=f"kind must be one of {sorted(EVENT_KINDS)}")
        out["kind"] = data["kind"]
    if "difficulty" in data and data["difficulty"] in DIFFICULTIES:
        out["difficulty"] = data["difficulty"]
    return out


async def organizer_overview(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    club = _club(request.app["content"], club_id)
    events = [_event_row(db, h) for h in db.hikes.for_club(club_id) if h.status != "cancelled"]
    upcoming = [e for e in events if not e["past"]]
    past = [e for e in events if e["past"]][::-1][:10]
    rated = [e["turnout"] for e in past if e["turnout"] is not None]
    return web.json_response({
        "club": {"id": club.id, "name": club.name},
        "followers": followers_count(db, club_id),
        "signups_upcoming": sum(e["going"] for e in upcoming),
        "avg_turnout": round(sum(rated) / len(rated)) if rated else None,
        "upcoming": upcoming, "past": past,
        "kinds": EVENT_KINDS, "difficulties": list(DIFFICULTIES),
    })


async def organizer_create(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    fields = _parse_event(await read_json_body(request))
    fields.setdefault("price", "Бесплатно")
    hike = db.hikes.create(club_id=club_id, **fields)
    club = _club(request.app["content"], club_id)
    for row in db.conn.execute("SELECT user_id FROM club_follows WHERE club_id = ?", (club_id,)).fetchall():
        notify(db, row["user_id"], f"{club.name}: новое событие «{hike.title}» — {when(hike)}.",
               event_link(hike))
    return web.json_response({"ok": True, "event": _event_row(db, hike)})


async def organizer_update(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    before = _own_event(request, club_id)
    fields = _parse_event(await read_json_body(request), partial=True)
    going_before = {s.user_id for s in db.hikes.signups(before.id) if s.status == "going"}
    hike = db.hikes.update(before.id, **fields)
    if (hike.start_at, hike.meeting_point) != (before.start_at, before.meeting_point):
        notify_signups(db, hike.id, f"Изменения в «{hike.title}»: {when(hike)}, "
                                    f"место: {hike.meeting_point or 'уточняется'}.", event_link(hike),
                       email_subject=f"Изменения: {hike.title}")
    for s in db.hikes.signups(hike.id):   # кого подняли из листа ожидания после увеличения мест
        if s.status == "going" and s.user_id not in going_before:
            promoted_notice(db, hike, s)
    return web.json_response({"ok": True, "event": _event_row(db, hike)})


async def organizer_cancel(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    hike = _own_event(request, club_id)
    notified = notify_signups(db, hike.id, f"Событие «{hike.title}» ({when(hike)}) отменено организатором.",
                              event_link(hike), email_subject=f"Отменено: {hike.title}")
    db.hikes.cancel_hike(hike.id)
    return web.json_response({"ok": True, "notified": notified})


async def organizer_duplicate(request: web.Request) -> web.Response:
    """Копия события через N дней (по умолчанию через неделю) — для регулярных встреч."""
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    src = _own_event(request, club_id)
    data = await read_json_body(request)
    try:
        days = max(1, min(int(data.get("days", 7)), 365))
    except (TypeError, ValueError):
        days = 7
    start = (datetime.fromisoformat(src.start_at) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
    copy = db.hikes.create(club_id=club_id, title=src.title, start_at=start, duration_hours=src.duration_hours,
                           meeting_point=src.meeting_point, route=src.route, difficulty=src.difficulty,
                           capacity=src.capacity, price=src.price, kind=src.kind, demo=src.demo)
    return web.json_response({"ok": True, "event": _event_row(db, copy)})


def _signup_rows(db: Database, hike_id: str) -> list[dict]:
    waiting = 0
    out = []
    for s in db.hikes.signups(hike_id):
        if s.status == "waitlist":
            waiting += 1
        out.append({"user_id": s.user_id, "name": s.full_name, "phone": s.phone, "status": s.status,
                    "waitlist_position": waiting if s.status == "waitlist" else None,
                    "signed_at": s.created_at, "attended": s.attended, "checked_in": s.checked_in,
                    "confirmed": s.confirmed == "yes", "src": s.src or "сайт"})
    return out


async def organizer_signups(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    hike = _own_event(request, club_id)
    rows = _signup_rows(db, hike.id)
    if request.query.get("format") == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["Имя", "Телефон", "Статус", "Подтвердил", "Источник", "Записался", "Пришёл"])
        for r in rows:
            writer.writerow([r["name"], r["phone"], "идёт" if r["status"] == "going" else "лист ожидания",
                             "да" if r["confirmed"] else "", r["src"],
                             r["signed_at"].replace("T", " ")[:16], "да" if r["attended"] else ""])
        return web.Response(body=("﻿" + buf.getvalue()).encode("utf-8"), content_type="text/csv",
                            charset="utf-8",
                            headers={"Content-Disposition": f'attachment; filename="signups-{hike.id}.csv"'})
    return web.json_response({"event": _event_row(db, hike), "signups": rows})


async def organizer_attendance(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    hike = _own_event(request, club_id)
    data = await read_json_body(request)
    try:
        key = int(data.get("user_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="user_id is required")
    if not db.hikes.set_attended(hike.id, key, bool(data.get("present", True))):
        raise web.HTTPNotFound(reason="signup not found")
    return web.json_response({"ok": True, "event": _event_row(db, hike)})


async def organizer_message(request: web.Request) -> web.Response:
    """Сообщение всем записанным на событие — приходит в колокольчик на сайте."""
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    hike = _own_event(request, club_id)
    text = str((await read_json_body(request)).get("text", "")).strip()[:MAX_TEXT]
    if not text:
        raise web.HTTPBadRequest(reason="text is required")
    sent = notify_signups(db, hike.id, f"{hike.title}: {text}", event_link(hike),
                          email_subject=f"Сообщение от организатора: {hike.title}")
    return web.json_response({"ok": True, "sent": sent})


def _club_leads(db: Database, club_id: str) -> list:
    events = CLUB_LEAD_EVENTS.get(club_id, ())
    if not events:
        return []
    marks = ",".join("?" * len(events))
    return db.conn.execute(f"SELECT * FROM site_leads WHERE event_id IN ({marks}) ORDER BY id DESC LIMIT 300",
                           events).fetchall()


async def organizer_leads(request: web.Request) -> web.Response:
    """Заявки из форм на странице клуба (билеты, волонтёры, запись на игру)."""
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    rows = _club_leads(db, club_id)
    if request.query.get("format") == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["Дата", "Имя", "Контакт", "Заявка", "Статус"])
        for r in rows:
            writer.writerow([r["created_at"].replace("T", " ")[:16], r["name"], r["contact"], r["message"],
                             LEAD_STATUSES.get(r["status"], r["status"])])
        return web.Response(body=("\ufeff" + buf.getvalue()).encode("utf-8"), content_type="text/csv", charset="utf-8",
                            headers={"Content-Disposition": f'attachment; filename="zayavki-{club_id}.csv"'})
    return web.json_response({
        "statuses": LEAD_STATUSES, "has_forms": bool(CLUB_LEAD_EVENTS.get(club_id)),
        "leads": [{"id": r["id"], "name": r["name"], "contact": r["contact"], "message": r["message"],
                   "status": r["status"], "at": r["created_at"]} for r in rows]})


async def organizer_lead_status(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    status = str((await read_json_body(request)).get("status", ""))
    if status not in LEAD_STATUSES:
        raise web.HTTPBadRequest(reason=f"status must be one of {sorted(LEAD_STATUSES)}")
    try:
        lead_id = int(request.match_info["lead_id"])
    except ValueError:
        raise web.HTTPNotFound(reason="lead not found")
    if lead_id not in {r["id"] for r in _club_leads(db, club_id)}:
        raise web.HTTPNotFound(reason="lead not found")
    with db.conn:
        db.conn.execute("UPDATE site_leads SET status = ? WHERE id = ?", (status, lead_id))
    return web.json_response({"ok": True})


def notify_club_about_lead(db: Database, event_id: str | None, name: str) -> None:
    """Новая заявка из формы клуба — сразу в колокольчик и на почту его организаторам."""
    for club_id, events in CLUB_LEAD_EVENTS.items():
        if event_id in events:
            for row in db.conn.execute("SELECT id FROM web_users WHERE role = 'organizer' AND club_id = ?",
                                       (club_id,)).fetchall():
                notify(db, row["id"], f"Новая заявка от {name}. Откройте «Кабинет клуба» → «Заявки».",
                       "#cabinet", email_subject="Новая заявка в Pople")


def setup_clubs(app: web.Application) -> None:
    app.on_startup.append(_start_jobs)
    app.on_cleanup.append(_stop_jobs)
    app.router.add_post("/api/rsvp", post_rsvp)
    app.router.add_post("/api/hikes/{hike_id}/confirm", post_confirm)
    app.router.add_get("/api/organizer/leads", organizer_leads)
    app.router.add_post("/api/organizer/leads/{lead_id}", organizer_lead_status)
    app.router.add_get("/api/notifications", get_notifications)
    app.router.add_post("/api/notifications/read", read_notifications)
    app.router.add_get("/api/clubs/mine", get_my_clubs)
    app.router.add_post("/api/clubs/{club_id}/follow", post_follow)
    app.router.add_get("/api/organizer/overview", organizer_overview)
    app.router.add_post("/api/organizer/events", organizer_create)
    app.router.add_post("/api/organizer/events/{event_id}", organizer_update)
    app.router.add_post("/api/organizer/events/{event_id}/cancel", organizer_cancel)
    app.router.add_post("/api/organizer/events/{event_id}/duplicate", organizer_duplicate)
    app.router.add_get("/api/organizer/events/{event_id}/signups", organizer_signups)
    app.router.add_post("/api/organizer/events/{event_id}/attendance", organizer_attendance)
    app.router.add_post("/api/organizer/events/{event_id}/message", organizer_message)
