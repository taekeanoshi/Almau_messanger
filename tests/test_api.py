"""Тесты HTTP API: aiohttp-приложение поднимается в памяти через TestClient, без сети."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

import bot.web_chat as web_chat_module
from bot.api import build_app
from bot.config import BASE_DIR
from bot.content import Content
from bot.db import Database


def make_client(admin_token=""):
    db = Database(":memory:")
    content = Content(BASE_DIR / "site" / "data")
    app = build_app(db, content, bot=None, admin_ids=frozenset(), admin_token=admin_token)
    return TestClient(TestServer(app)), db, content


def run(coro):
    return asyncio.run(coro)


def test_health():
    async def go():
        client, db, content = make_client()
        async with client:
            resp = await client.get("/health")
            assert resp.status == 200
            assert (await resp.json())["status"] == "ok"

    run(go())


def test_clubs_and_events_served_from_backend():
    async def go():
        client, db, content = make_client()
        async with client:
            resp = await client.get("/api/clubs")
            assert resp.status == 200
            assert len(await resp.json()) == len(content.clubs)

            ev = content.events[0]
            db.set_going(1, ev.id, True)

            resp = await client.get("/api/events")
            data = await resp.json()
            match = next(e for e in data if e["id"] == ev.id)
            assert match["going"] == 1

            resp = await client.get(f"/api/events/{ev.id}")
            assert (await resp.json())["going"] == 1

            resp = await client.get("/api/events/does-not-exist")
            assert resp.status == 404
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    run(go())


def test_cors_preflight_allows_authorization_header():
    """Сайт и API на разных доменах: без Authorization в Allow-Headers браузер блокирует
    preflight и ни один запрос с Bearer-токеном (то есть всё после входа) не доходит."""
    async def go():
        client, db, content = make_client()
        async with client:
            resp = await client.options("/api/chat/me", headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            })
            assert resp.status == 200
            allowed = {h.strip().lower() for h in resp.headers["Access-Control-Allow-Headers"].split(",")}
            assert {"authorization", "content-type"} <= allowed

            # И на ошибках (401) CORS-заголовки тоже есть — иначе фронт видит «сеть упала», а не 401
            resp = await client.get("/api/chat/me", headers={"Authorization": "Bearer nope"})
            assert resp.status == 401
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    run(go())


def test_unexpected_server_error_still_carries_cors_headers(monkeypatch):
    """Падение обработчика не должно выглядеть для сайта как обрыв сети: ответ 500 приходит
    с CORS-заголовками и без подробностей ошибки."""
    async def go():
        client, db, content = make_client()

        def boom(*args, **kwargs):
            raise RuntimeError("secret internals")

        monkeypatch.setattr(db, "stats", boom)
        async with client:
            resp = await client.get("/api/stats")
            assert resp.status == 500
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
            assert "secret internals" not in await resp.text()

    run(go())


def test_post_lead_saves_to_db_and_rejects_incomplete():
    async def go():
        client, db, content = make_client()
        async with client:
            resp = await client.post(
                "/api/leads", json={"name": "Аян", "contact": "@ayan", "message": "Хочу демо"}
            )
            assert resp.status == 201
            body = await resp.json()
            assert body["ok"] is True
            assert db.leads_count() == 1
            assert db.recent_leads(1)[0]["name"] == "Аян"

            resp = await client.post("/api/leads", json={"name": "", "contact": ""})
            assert resp.status == 400
            assert db.leads_count() == 1

    run(go())


def test_public_stats_hide_internal_counters():
    """Публичная статистика — только цифры для главной; заявки, жалобы и баны наружу не отдаются."""
    async def go():
        client, db, content = make_client()
        async with client:
            db.add_lead("Аян", "@ayan", "", None)
            resp = await client.get("/api/stats")
            data = await resp.json()
            assert set(data) == {"users", "dialogs_total", "event_dialogs"}

    run(go())


def test_post_event_going_requires_a_registered_account(monkeypatch):
    sent = {}

    async def fake_send(app, to_email, code, purpose="verify"):
        sent[to_email] = code

    monkeypatch.setattr(web_chat_module, "_send_otp_email", fake_send)

    async def go():
        client, db, content = make_client()
        async with client:
            ev = content.events[0]

            # Без токена — 401, анонимно отметиться больше нельзя
            resp = await client.post(f"/api/events/{ev.id}/going", json={"going": True})
            assert resp.status == 401

            await client.post("/api/chat/register", json={
                "first_name": "Аян", "last_name": "Касым", "phone": "+77001234567",
                "email": "ayan@example.com", "password": "secret-pw-1", "gender": "male",
                "course": "2", "specialty": "Computer Science",
            })
            verify = await client.post("/api/chat/otp/verify",
                                       json={"email": "ayan@example.com", "code": sent["ayan@example.com"]})
            token = (await verify.json())["token"]
            headers = {"Authorization": f"Bearer {token}"}

            resp = await client.post(f"/api/events/{ev.id}/going", json={"going": True}, headers=headers)
            assert resp.status == 200
            body = await resp.json()
            assert body == {"ok": True, "going": True, "count": 1}

            # Повторная отметка тем же аккаунтом не плодит дубликаты
            await client.post(f"/api/events/{ev.id}/going", json={"going": True}, headers=headers)
            resp = await client.get(f"/api/events/{ev.id}")
            assert (await resp.json())["going"] == 1

            resp = await client.post(f"/api/events/{ev.id}/going", json={"going": False}, headers=headers)
            assert (await resp.json())["count"] == 0

            resp = await client.post("/api/events/does-not-exist/going", json={"going": True}, headers=headers)
            assert resp.status == 404

    run(go())


def test_get_leads_requires_admin_token():
    async def go():
        client, db, content = make_client(admin_token="secret-pw")
        async with client:
            db.add_lead("Аян", "@ayan", "", None)

            resp = await client.get("/api/leads")
            assert resp.status == 401

            resp = await client.get("/api/leads", params={"token": "wrong"})
            assert resp.status == 401

            resp = await client.get("/api/leads", params={"token": "secret-pw"})
            assert resp.status == 200
            data = await resp.json()
            assert len(data) == 1 and data[0]["name"] == "Аян"

    run(go())


def test_get_leads_disabled_without_admin_token_configured():
    async def go():
        client, db, content = make_client()  # admin_token не задан
        async with client:
            resp = await client.get("/api/leads", params={"token": ""})
            assert resp.status == 401

    run(go())


def test_admin_reset_web_chat_requires_token_and_confirmation():
    async def go():
        client, db, content = make_client(admin_token="secret-pw")
        async with client:
            await client.post("/api/chat/register", json={
                "first_name": "Аян", "last_name": "Касым", "phone": "+77001234567",
                "email": "ayan@example.com", "password": "secret-pw-1", "gender": "male",
                "course": "2", "specialty": "Computer Science",
            })
            assert db.stats()["web_users"] == 1

            # Без токена — 401, данные целы
            resp = await client.post("/api/admin/reset-web-chat")
            assert resp.status == 401
            assert db.stats()["web_users"] == 1

            # С токеном, но без явного confirm=yes — тоже отказ, данные целы
            resp = await client.post("/api/admin/reset-web-chat", params={"token": "secret-pw"})
            assert resp.status == 400
            assert db.stats()["web_users"] == 1

            # Токен + confirm=yes — стирает все web_* таблицы и возвращает, сколько удалено
            resp = await client.post("/api/admin/reset-web-chat", params={"token": "secret-pw", "confirm": "yes"})
            assert resp.status == 200
            body = await resp.json()
            assert body["deleted"]["web_users"] == 1
            assert db.stats()["web_users"] == 0

    run(go())


def test_hikes_api_shows_free_spots_and_never_leaks_personal_data():
    async def go():
        client, db, content = make_client()
        hike = db.hikes.create(club_id="summit", title="Тропа", start_at="2099-12-31T07:00", capacity=2)
        db.hikes.create(club_id="other", title="Чужой клуб", start_at="2099-12-31T08:00", capacity=5)
        db.hikes.signup(hike.id, 1, "Секретное Имя", "+77071234567")
        async with client:
            resp = await client.get("/api/hikes?club=summit")
            assert resp.status == 200
            (item,) = await resp.json()
            assert (item["id"], item["going"], item["spots_left"], item["capacity"]) == (hike.id, 1, 1, 2)
            raw = await (await client.get("/api/hikes")).text()
            assert "Секретное Имя" not in raw and "+77071234567" not in raw
            assert len(await (await client.get("/api/hikes")).json()) == 2

            one = await client.get(f"/api/hikes/{hike.id}")
            assert one.status == 200 and (await one.json())["title"] == "Тропа"
            assert (await client.get("/api/hikes/нет")).status == 404

    run(go())


def test_cancelled_and_finished_hikes_are_not_listed():
    async def go():
        client, db, content = make_client()
        gone = db.hikes.create(club_id="summit", title="Отменён", start_at="2099-12-31T07:00")
        db.hikes.cancel_hike(gone.id)
        db.hikes.create(club_id="summit", title="Был давно", start_at="2020-01-01T07:00")
        async with client:
            assert await (await client.get("/api/hikes?club=summit")).json() == []

    run(go())
