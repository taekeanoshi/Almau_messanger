from bot.db import Database
from bot.demo_people import DEMO_PEOPLE, seed_demo_people


def test_seed_adds_150_people_once(tmp_path):
    db = Database(tmp_path / "t.db")
    assert len(DEMO_PEOPLE) == 10
    assert seed_demo_people(db) == 150
    assert seed_demo_people(db) == 0  # повторный старт сервиса не плодит дублей
    rows = db.search_web_users("", exclude_id=0, limit=500)
    assert len(rows) == 150
    assert len({(r["first_name"], r["last_name"]) for r in rows}) == 150  # все имена разные
    assert all(r["is_demo"] == 1 for r in rows)
    assert all(r["email_verified"] == 1 and r["role"] == "student" for r in rows)
    # казахские буквы ищутся без учёта регистра
    assert [r["first_name"] for r in db.search_web_users("ӘСЕЛ", exclude_id=0)] == ["Әсел"]


def test_seed_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_DEMO_PEOPLE", "0")
    db = Database(tmp_path / "t.db")
    assert seed_demo_people(db) == 0
    assert db.search_web_users("", exclude_id=0) == []


def test_demo_accounts_are_labelled_hidden_from_public_stats_and_removable_by_admin(monkeypatch):
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    import bot.web_chat as web_chat_module
    from bot.api import build_app
    from bot.config import BASE_DIR
    from bot.content import Content

    sent = {}

    async def fake_send(app, to_email, code, purpose="verify"):
        sent[to_email] = code

    monkeypatch.setattr(web_chat_module, "_send_otp_email", fake_send)

    async def go():
        db = Database(":memory:")
        seed_demo_people(db)
        app = build_app(db, Content(BASE_DIR / "site" / "data"), admin_token="tok")
        async with TestClient(TestServer(app)) as client:
            async def register(n, email):
                resp = await client.post("/api/chat/register", json={
                    "first_name": "Реал", "last_name": f"Номер{n}", "phone": f"+7701000000{n}", "email": email,
                    "password": "secret-pw-1", "gender": "male", "course": "2", "specialty": "IT"})
                assert resp.status == 201
                resp = await client.post("/api/chat/otp/verify", json={"email": email, "code": sent[email]})
                return await resp.json()

            student = await register(1, "real1@example.org")
            boss = await register(2, "boss@example.org")
            await client.post("/api/admin/set-role", headers={"X-Admin-Token": "tok"},
                              json={"email": "boss@example.org", "role": "admin"})
            h = {"Authorization": f"Bearer {student['token']}"}

            # Все 150 демо-людей видны в поиске, и по ответу не отличить их от остальных
            resp = await client.get("/api/chat/direct/search-users", headers=h)
            users = (await resp.json())["users"]
            assert len(users) == 151 and all("demo" not in u for u in users)

            # В публичной статистике и в цифрах админки они не считаются настоящими
            assert (await (await client.get("/api/stats")).json())["users"] == 2
            ah = {"Authorization": f"Bearer {boss['token']}"}
            overview = await (await client.get("/api/admin/overview", headers=ah)).json()
            assert overview["demo_users"] == 150 and overview["users"] == 2

            # Студент не управляет демо-данными; админ удаляет их одной кнопкой
            assert (await client.post("/api/admin/demo-users/delete", headers=h)).status == 403
            resp = await client.post("/api/admin/demo-users/delete", headers=ah)
            assert (await resp.json())["removed"] == 150
            resp = await client.get("/api/chat/direct/search-users", headers=h)
            assert len((await resp.json())["users"]) == 1

            # и создаёт заново
            resp = await client.post("/api/admin/demo-users/seed", headers=ah, json={"count": 30})
            assert (await resp.json())["count"] == 30

    asyncio.run(go())
