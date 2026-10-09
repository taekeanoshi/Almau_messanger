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


def test_post_event_going_works_without_telegram():
    async def go():
        client, db, content = make_client()
        async with client:
            ev = content.events[0]

            resp = await client.post(f"/api/events/{ev.id}/going",
                                     json={"visitor_id": "abc123", "going": True})
            assert resp.status == 200
            body = await resp.json()
            assert body == {"ok": True, "going": True, "count": 1}
            assert db.is_going("web:abc123", ev.id)

            # Повторная отметка тем же visitor_id не плодит дубликаты
            await client.post(f"/api/events/{ev.id}/going", json={"visitor_id": "abc123", "going": True})
            resp = await client.get(f"/api/events/{ev.id}")
            assert (await resp.json())["going"] == 1

            resp = await client.post(f"/api/events/{ev.id}/going",
                                     json={"visitor_id": "abc123", "going": False})
            assert (await resp.json())["count"] == 0

            resp = await client.post("/api/events/does-not-exist/going", json={"visitor_id": "abc123"})
            assert resp.status == 404

            resp = await client.post(f"/api/events/{ev.id}/going", json={"going": True})
            assert resp.status == 400

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
