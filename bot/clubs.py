"""Клубы: подписки студентов, уведомления и кабинет организатора.

События клуба хранятся там же, где походы (bot/hikes.py, таблица hikes с полем kind):
запись, лимит мест и лист ожидания уже работали для Summit Seekers — кабинет даёт
организатору любого клуба управлять ими самому. Организатор видит и меняет только
свой клуб (web_users.club_id); админ может открыть кабинет любого клуба: ?club=<id>.
"""
import csv
import io
from datetime import datetime, timedelta

from aiohttp import web

from bot.content import Content
from bot.db import Database
from bot.hikes import DIFFICULTIES, EVENT_KINDS, now as hikes_now
from bot.web_chat import read_json_body, require_session

MAX_TEXT = 300
REMIND_BEFORE = timedelta(hours=24)


def hike_key(web_user_id: int) -> int:
    """В записях на события аккаунты сайта лежат под отрицательным id (исторически плюс — Telegram)."""
    return -web_user_id


def _stamp() -> str:
    return hikes_now().isoformat(timespec="seconds")


# ---------- уведомления ----------

def notify(db: Database, web_user_id: int, text: str, link: str = "") -> None:
    if web_user_id <= 0:
        return
    with db.conn:
        db.conn.execute("INSERT INTO notifications (user_id, text, link, created_at) VALUES (?, ?, ?, ?)",
                        (web_user_id, text[:MAX_TEXT], link[:120], _stamp()))


def notify_signups(db: Database, hike_id: str, text: str, link: str = "") -> int:
    """Сообщение всем, кто записан на событие (и идущим, и из листа ожидания)."""
    signups = db.hikes.signups(hike_id)
    for s in signups:
        notify(db, -s.user_id, text, link)
    return len(signups)


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря")


def when(hike) -> str:
    """«2026-12-12T19:00» → «12 декабря в 19:00» — так дата читается в уведомлениях."""
    d = datetime.fromisoformat(hike.start_at)
    return f"{d.day} {_MONTHS[d.month - 1]} в {d:%H:%M}"


def event_link(hike) -> str:
    return "#summit" if hike.club_id == "summit" else "#events"


def _add_due_reminders(db: Database, web_user_id: int) -> None:
    """Напоминание за сутки создаётся в момент, когда человек открывает сайт: фоновых задач нет."""
    at = hikes_now()
    for row in db.conn.execute(
            "SELECT hike_id FROM hike_signups WHERE user_id = ? AND status = 'going' AND reminded_24h = 0",
            (hike_key(web_user_id),)).fetchall():
        hike = db.hikes.get(row["hike_id"])
        if hike and hike.is_open and at <= hike.start <= at + REMIND_BEFORE:
            notify(db, web_user_id, f"Напоминание: «{hike.title}» уже скоро — {when(hike)}. "
                                    f"Место: {hike.meeting_point or 'уточняется'}.", event_link(hike))
            db.hikes.mark_reminded(hike.id, hike_key(web_user_id), "24h")


async def get_notifications(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    _add_due_reminders(db, user_id)
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
    return {**h.public_dict(going, waitlist), "attended": attended, "past": h.end < hikes_now(),
            "turnout": round(attended * 100 / going) if going and attended else None}


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
                                    f"место: {hike.meeting_point or 'уточняется'}.", event_link(hike))
    for s in db.hikes.signups(hike.id):   # кого подняли из листа ожидания после увеличения мест
        if s.status == "going" and s.user_id not in going_before:
            notify(db, -s.user_id, f"Освободилось место: вы записаны на «{hike.title}».", event_link(hike))
    return web.json_response({"ok": True, "event": _event_row(db, hike)})


async def organizer_cancel(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    hike = _own_event(request, club_id)
    notified = notify_signups(db, hike.id, f"Событие «{hike.title}» отменено организатором.", event_link(hike))
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
                    "signed_at": s.created_at, "attended": s.attended, "checked_in": s.checked_in})
    return out


async def organizer_signups(request: web.Request) -> web.Response:
    _, club_id = _organizer_club(request)
    db: Database = request.app["db"]
    hike = _own_event(request, club_id)
    rows = _signup_rows(db, hike.id)
    if request.query.get("format") == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["Имя", "Телефон", "Статус", "Записался", "Пришёл"])
        for r in rows:
            writer.writerow([r["name"], r["phone"], "идёт" if r["status"] == "going" else "лист ожидания",
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
    sent = notify_signups(db, hike.id, f"{hike.title}: {text}", event_link(hike))
    return web.json_response({"ok": True, "sent": sent})


def setup_clubs(app: web.Application) -> None:
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
