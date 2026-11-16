from bot.db import Database
from bot.demo_people import DEMO_PEOPLE, seed_demo_people


def test_seed_adds_ten_people_once(tmp_path):
    db = Database(tmp_path / "t.db")
    assert len(DEMO_PEOPLE) == 10
    assert seed_demo_people(db) == 10
    assert seed_demo_people(db) == 0  # повторный старт сервиса не плодит дублей
    rows = db.search_web_users("", exclude_id=0)
    assert len(rows) == 10
    assert all(r["email_verified"] == 1 and r["role"] == "student" for r in rows)
    # казахские буквы ищутся без учёта регистра
    assert [r["first_name"] for r in db.search_web_users("ӘСЕЛ", exclude_id=0)] == ["Әсел"]


def test_seed_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_DEMO_PEOPLE", "0")
    db = Database(tmp_path / "t.db")
    assert seed_demo_people(db) == 0
    assert db.search_web_users("", exclude_id=0) == []
