"""Тесты HTTP API: aiohttp-приложение поднимается в памяти через TestClient, без сети."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

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


def test_stats_reports_leads_count():
    async def go():
        client, db, content = make_client()
        async with client:
            db.add_lead("Аян", "@ayan", "", None)
            resp = await client.get("/api/stats")
            data = await resp.json()
            assert data["leads"] == 1

    run(go())


def test_post_event_going_requires_a_registered_account():
    async def go():
        client, db, content = make_client()
        async with client:
            ev = content.events[0]

            # Без токена — 401, анонимно отметиться больше нельзя
            resp = await client.post(f"/api/events/{ev.id}/going", json={"going": True})
            assert resp.status == 401

            reg = await client.post("/api/chat/register", json={
                "first_name": "Аян", "last_name": "Касым", "phone": "+77001234567",
                "password": "secret1", "gender": "male",
            })
            token = (await reg.json())["token"]
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
        client, db, content = make_client(admin_token="secret")
        async with client:
            db.add_lead("Аян", "@ayan", "", None)

            resp = await client.get("/api/leads")
            assert resp.status == 401

            resp = await client.get("/api/leads", params={"token": "wrong"})
            assert resp.status == 401

            resp = await client.get("/api/leads", params={"token": "secret"})
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
