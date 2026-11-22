"""Учебный план AlmaU: специальности по курсам, каталог, запись студента, текущие оценки."""
from bot import curriculum as cm
from bot.db import Database
from bot.demo_people import seed_demo_people


def _student(db, first="Тест", last="Студент", course="3", specialty="Software Engineering", n=1):
    uid = db.create_web_user(first, last, f"+7702000000{n}", f"s{n}@uni.kz", "h", "s", "male", course, specialty, "nick")
    db.mark_web_email_verified(f"s{n}@uni.kz")
    return uid


def test_options_list_specialties_per_course():
    opts = cm.get_curriculum().options()
    assert "Software Engineering" in opts["courses"]["3"]
    assert opts["with_schedule"]["4"] == ["Вычислительная техника и ПО (МВД)"]
    assert len(opts["courses"]["4"]) == len(cm.get_curriculum().specialties)  # на 4 курсе можно выбрать любую


def test_student_gets_conflict_free_schedule_and_no_grades(tmp_path):
    db = Database(tmp_path / "t.db")
    cm.ensure_catalog(db)
    uid = _student(db)
    cm.on_profile_changed(db, uid)
    items = db.schedule_for_student(uid)
    titles = {i["course_title"] for i in items}
    assert titles == set(cm.get_curriculum().subjects("Software Engineering", "3"))
    assert not any(cm._overlaps(a, b) for i, a in enumerate(items) for b in items[i + 1:])
    assert all(i["teacher_id"] for i in items)
    assert db.grades_for_student(uid) == []  # новые студенты без оценок

    # смена специальности: старые дисциплины каталога снимаются, новые добавляются
    db.update_web_profile(uid, {"course": "1", "specialty": "Маркетинг"})
    cm.on_profile_changed(db, uid)
    assert {i["course_title"] for i in db.schedule_for_student(uid)} == set(cm.get_curriculum().subjects("Маркетинг", "1"))


def test_demo_and_showcase_students_get_current_grades_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SEED_DEMO_PEOPLE", "5")
    db = Database(tmp_path / "t.db")
    seed_demo_people(db)
    hamid = _student(db, "Хамид", "Хасанов", n=7)
    other = _student(db, n=8)
    cm.sync_all(db)
    for uid in [hamid] + [r["id"] for r in db.conn.execute("SELECT id FROM web_users WHERE is_demo = 1 AND role = 'student'")]:
        grades = db.grades_for_student(uid)
        assert grades and {g["label"] for g in grades} == {"ТК1", "РК1", "ТК2"}  # РК2 и сессии ещё не было
        assert all(35 <= g["score"] <= 100 for g in grades)
    assert db.grades_for_student(other) == []
    cm.sync_all(db)  # повторный старт ничего не дублирует
    assert len(db.grades_for_student(hamid)) == 3 * len(db.courses_for_student(hamid))
    # преподаватели из файлов не удаляются вместе с демо-студентами
    db.delete_demo_users()
    assert db.conn.execute("SELECT COUNT(*) FROM web_users WHERE role = 'teacher'").fetchone()[0] > 100
