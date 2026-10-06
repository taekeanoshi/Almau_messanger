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
