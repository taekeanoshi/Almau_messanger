"""Админ-панель сайта: всё, что раньше делалось только curl-запросами с ADMIN_TOKEN,
доступно аккаунту с ролью admin прямо из интерфейса (вкладка «Админка»).

Доступ — по обычной сессии, но только для role == 'admin'. Первого админа назначают
через POST /api/admin/set-role с ADMIN_TOKEN (bot/api.py) — дальше он управляет остальными сам.
"""
import secrets

from aiohttp import web

from bot.db import Database
from bot.web_chat import _hash_invite_key, read_json_body, require_session

VALID_ROLES = {"student", "teacher", "registrar", "admin"}
INVITE_KEY_ROLES = {"teacher", "registrar"}


def _require_admin(request: web.Request):
    user_id = require_session(request)
    row = request.app["db"].get_web_user(user_id)
    if row is None or row["role"] != "admin":
        raise web.HTTPForbidden(reason="admin role required")
    return row


def _admin_user(row) -> dict:
    return {
        "id": row["id"], "first_name": row["first_name"], "last_name": row["last_name"],
        "email": row["email"], "phone": row["phone"], "role": row["role"],
        "banned": bool(row["banned"]), "email_verified": bool(row["email_verified"]),
        "course": row["course"], "specialty": row["specialty"], "created_at": row["created_at"],
    }


def _target_user_id(data: dict) -> int:
    try:
        return int(data.get("user_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="user_id is required")


async def overview(request: web.Request) -> web.Response:
    _require_admin(request)
    db: Database = request.app["db"]
    stats = db.admin_overview()
    stats["searching_now"] = request.app["web_mm"].waiting_count()
    return web.json_response(stats)


async def list_users(request: web.Request) -> web.Response:
    _require_admin(request)
    db: Database = request.app["db"]
    rows = db.list_web_users(request.query.get("q", "")[:120])
    return web.json_response({"users": [_admin_user(r) for r in rows]})


async def set_role(request: web.Request) -> web.Response:
    admin = _require_admin(request)
    data = await read_json_body(request)
    target_id = _target_user_id(data)
    role = str(data.get("role", "")).strip().lower()
    if role not in VALID_ROLES:
        raise web.HTTPBadRequest(reason=f"role must be one of {sorted(VALID_ROLES)}")
    db: Database = request.app["db"]
    target = db.get_web_user(target_id)
    if target is None:
        raise web.HTTPNotFound(reason="user not found")
    # Последнего админа нельзя разжаловать — иначе в панель больше никто не войдёт.
    if target["role"] == "admin" and role != "admin" and db.count_web_admins() <= 1:
        raise web.HTTPConflict(reason="can't demote the last admin")
    db.set_web_user_role_by_id(target_id, role)
    if role not in ("student", "admin"):
        request.app["web_mm"].leave(target_id)  # сотрудникам анонимный чат недоступен
    return web.json_response({"ok": True, "user": _admin_user(db.get_web_user(target_id)), "by": admin["id"]})


async def set_banned(request: web.Request) -> web.Response:
    admin = _require_admin(request)
    data = await read_json_body(request)
    target_id = _target_user_id(data)
    banned = bool(data.get("banned", True))
    if target_id == admin["id"] and banned:
        raise web.HTTPBadRequest(reason="can't ban yourself")
    db: Database = request.app["db"]
    if not db.set_web_user_banned(target_id, banned):
        raise web.HTTPNotFound(reason="user not found")
    if banned:
        request.app["web_mm"].leave(target_id)
    return web.json_response({"ok": True, "user": _admin_user(db.get_web_user(target_id))})


async def rotate_invite_key(request: web.Request) -> web.Response:
    """Новый ключ-приглашение для преподавателей или отдела расписания. Старый сразу
    перестаёт работать; сырой ключ показывается один раз и нигде не хранится."""
    _require_admin(request)
    data = await read_json_body(request)
    role = str(data.get("role", "")).strip().lower()
    if role not in INVITE_KEY_ROLES:
        raise web.HTTPBadRequest(reason=f"role must be one of {sorted(INVITE_KEY_ROLES)}")
    key = secrets.token_urlsafe(18)
    request.app["db"].set_invite_key(role, _hash_invite_key(key))
    return web.json_response({"ok": True, "role": role, "invite_key": key})


async def leads(request: web.Request) -> web.Response:
    _require_admin(request)
    db: Database = request.app["db"]
    return web.json_response({"leads": [dict(row) for row in db.recent_leads(100)]})


async def reports(request: web.Request) -> web.Response:
    """Жалобы с последними сообщениями диалога — модератору нужно видеть, на что жалуются.
    Сообщения показываются только для диалогов, на которые есть жалоба."""
    _require_admin(request)
    db: Database = request.app["db"]
    only_open = request.query.get("all") != "1"
    out = []
    for r in db.web_reports(only_open=only_open):
        messages = [{"from_target": m["sender_id"] == r["target_id"], "kind": m["kind"],
                     "text": m["text"] if m["kind"] != "photo" else "📷 Фото", "at": m["created_at"]}
                    for m in db.recent_dialog_messages(r["dialog_id"])]
        out.append({
            "id": r["id"], "created_at": r["created_at"], "reason": r["reason"], "resolved": bool(r["resolved"]),
            "reporter": {"id": r["reporter_id"],
                         "name": f"{r['reporter_first'] or '—'} {r['reporter_last'] or ''}".strip()},
            "target": {"id": r["target_id"], "name": f"{r['target_first'] or '—'} {r['target_last'] or ''}".strip(),
                       "banned": bool(r["target_banned"]), "reporters": r["target_reporters"]},
            "messages": messages,
        })
    return web.json_response({"reports": out})


async def resolve_report(request: web.Request) -> web.Response:
    _require_admin(request)
    data = await read_json_body(request)
    try:
        report_id = int(data.get("report_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="report_id is required")
    if not request.app["db"].resolve_web_report(report_id):
        raise web.HTTPNotFound(reason="report not found")
    return web.json_response({"ok": True})


async def hike_signups(request: web.Request) -> web.Response:
    """Кто записался на походы — имена и телефоны видит только админ."""
    _require_admin(request)
    db: Database = request.app["db"]
    out = []
    for hike in db.hikes.upcoming():
        going, waitlist = db.hikes.counts(hike.id)
        out.append({
            **hike.public_dict(going, waitlist),
            "signups": [{"full_name": s.full_name, "phone": s.phone, "status": s.status,
                         "src": s.src, "checked_in": s.checked_in} for s in db.hikes.signups(hike.id)],
        })
    return web.json_response({"hikes": out})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/admin/overview", overview)
    app.router.add_get("/api/admin/users", list_users)
    app.router.add_post("/api/admin/users/role", set_role)
    app.router.add_post("/api/admin/users/ban", set_banned)
    app.router.add_post("/api/admin/invite-key", rotate_invite_key)
    app.router.add_get("/api/admin/leads", leads)
    app.router.add_get("/api/admin/reports", reports)
    app.router.add_post("/api/admin/reports/resolve", resolve_report)
    app.router.add_get("/api/admin/hike-signups", hike_signups)
