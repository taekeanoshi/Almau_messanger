import sqlite3

from bot.db import Database


def make_db():
    db = Database(":memory:")
    db.save_profile(1, "AlmaU", "2 курс", {"IT", "Спорт"})
    db.save_profile(2, "KBTU", "1 курс", set())
    return db


def test_profile_roundtrip_and_update_keeps_socials():
    db = make_db()
    db.set_socials(1, "inst @me")
    db.save_profile(1, "SDU", "3 курс", {"Кино"})
    u = db.get_user(1)
    assert (u.university, u.course, u.interests, u.socials) == ("SDU", "3 курс", frozenset({"Кино"}), "inst @me")
    assert db.get_user(999) is None


def test_reports_count_distinct_reporters():
    db = make_db()
    assert db.add_report(1, 2) == 1
    assert db.add_report(1, 2) == 1
    db.save_profile(3, "AlmaU", "1 курс", set())
    assert db.add_report(3, 2) == 2


def test_ban():
    db = make_db()
    assert db.set_banned(2, True) and db.is_banned(2)
    assert not db.set_banned(42, True)
    db.set_banned(2, False)
    assert not db.is_banned(2)


def test_going_and_stats():
    db = make_db()
    db.set_going(1, "halloween", True)
    db.set_going(1, "halloween", True)
    db.set_going(2, "halloween", True)
    assert db.going_count("halloween") == 2 and db.is_going(1, "halloween")
    db.set_going(2, "halloween", False)
    assert db.going_count("halloween") == 1
    db.log_dialog(1, 2)
    db.log_dialog(1, 2, "halloween")
    s = db.stats()
    assert s["users"] == 2 and s["dialogs_total"] == 2 and s["dialogs_today"] == 2 and s["event_dialogs"] == 1


def test_opening_db_with_outdated_web_users_schema_adds_missing_columns(tmp_path):
    """Имитирует БД, пережившую деплой со старой схемой web_users (например, без email,
    password_hash/password_salt или email_verified) — Database должен сам доковать колонки,
    а не падать на INSERT с 'no such column'."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE web_users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name  TEXT NOT NULL,
            last_name   TEXT NOT NULL,
            phone       TEXT NOT NULL UNIQUE,
            gender      TEXT NOT NULL,
            banned      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    db = Database(db_path)
    user_id = db.create_web_user("Хамит", "Хасанов", "+77001234567", "hamit@example.com",
                                 "hash", "salt", "male")
    row = db.get_web_user(user_id)
    assert row["email"] == "hamit@example.com"
    assert row["email_verified"] == 0
