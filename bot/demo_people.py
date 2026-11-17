"""Демо-студенты для вкладки «Люди» и прямого чата: 150 аккаунтов с казахскими именами.

База SQLite на Railway сбрасывается при каждом деплое (см. README, «Хостинг»), поэтому
людей не вставляем руками один раз, а досеиваем при каждом старте сервиса — как походы
из site/data/hikes.json. Сид идемпотентный: аккаунт с такой почтой уже есть — пропускаем.

Это витрина, а не настоящие люди: почты на зарезервированном домене example.com (письма
туда не уходят), телефоны фиктивные, а пароль заведомо невалидный — войти под ними
нельзя, ни один хеш PBKDF2 не начинается с «!». Все они помечены is_demo: в «Людях» рядом с
именем стоит значок «демо», в публичную статистику они не входят, а админ удаляет их одной
кнопкой. Сколько создавать — SEED_DEMO_PEOPLE (по умолчанию 150, 0 — не создавать).
"""
import logging
import os
import random
import secrets

from bot.db import Database

logger = logging.getLogger(__name__)

# (имя, фамилия, пол, курс, специальность, анонимный ник)
DEMO_PEOPLE = [
    ("Аружан", "Сейтқали", "female", "2", "Маркетинг", "aru_sky"),
    ("Нұрсұлтан", "Жақсыбеков", "male", "3", "Информационные системы", "nurs_dev"),
    ("Айгерим", "Тұрсынбаева", "female", "1", "Финансы", "aigerim_fin"),
    ("Дастан", "Омаров", "male", "4", "Менеджмент", "dastan_go"),
    ("Мадина", "Әбдірахманова", "female", "2", "Digital-маркетинг", "madi_smm"),
    ("Ерасыл", "Нұрланұлы", "male", "1", "Computer Science", "erasyl_cs"),
    ("Жансая", "Қайратқызы", "female", "3", "Туризм и гостеприимство", "zhansaya_trip"),
    ("Бекзат", "Серікбаев", "male", "2", "Экономика", "bekzat_eco"),
    ("Әсел", "Мұратова", "female", "4", "Бизнес-аналитика", "asel_data"),
    ("Темірлан", "Асқаров", "male", "3", "Логистика", "temir_log"),
]


DEFAULT_COUNT = 150
_MALE = ["Айдар", "Ержан", "Данияр", "Аян", "Руслан", "Тимур", "Арман", "Нурбол", "Алихан", "Бауыржан", "Мирас",
         "Нурлан", "Санжар", "Ернар", "Азамат", "Жандос", "Куат", "Расул", "Ильяс", "Алмас", "Бекжан", "Нұрбек",
         "Қанат", "Мақсат"]
_FEMALE = ["Айым", "Томирис", "Динара", "Камила", "Малика", "Алина", "Сабина", "Аида", "Дана", "Ляззат", "Назерке",
           "Гүлнар", "Салтанат", "Асем", "Жанель", "Ару", "Карина", "Мерей", "Балжан", "Іңкәр", "Сымбат", "Акмарал"]
_SURNAMES = ["Ахметов", "Бекенов", "Султанов", "Қасымов", "Жумабаев", "Нургалиев", "Садыков", "Тулегенов", "Мусин",
             "Абенов", "Кенжебаев", "Оспанов", "Рахимов", "Искаков", "Сарсенов", "Калиев", "Дүйсенов", "Темирбеков",
             "Байжанов", "Утегенов", "Аманжолов", "Бисенов", "Ержанов", "Мукашев"]
_COURSES = ["1", "2", "3", "4", "Магистратура"]
_SPECIALTIES = ["Маркетинг", "Финансы", "Менеджмент", "Экономика", "Computer Science", "Информационные системы",
                "Бизнес-аналитика", "Логистика", "Туризм и гостеприимство", "Digital-маркетинг", "Учёт и аудит",
                "Государственное управление"]


def _generated_people(total: int) -> list[tuple]:
    """Детерминированный набор: при каждом старте получаются те же люди, поэтому сид идемпотентен."""
    rng = random.Random(2026)
    people = list(DEMO_PEOPLE[:total])
    seen = {(p[0], p[1]) for p in people}
    while len(people) < total:
        female = rng.random() < 0.5
        first = rng.choice(_FEMALE if female else _MALE)
        last = rng.choice(_SURNAMES) + ("а" if female else "")
        if (first, last) in seen:
            continue
        seen.add((first, last))
        people.append((first, last, "female" if female else "male", rng.choice(_COURSES),
                       rng.choice(_SPECIALTIES), f"Аноним-{1000 + len(people)}"))
    return people


def _demo_email(index: int) -> str:
    return f"demo{index + 1:02d}@example.com"


def _demo_phone(index: int) -> str:
    return f"+7700000{index + 1:04d}"


def demo_count_from_env() -> int:
    raw = os.getenv("SEED_DEMO_PEOPLE", str(DEFAULT_COUNT)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_COUNT
    return DEFAULT_COUNT if value == 1 else max(0, min(value, 500))  # «1» — старое значение «включено»


def seed_demo_people(db: Database, count: int | None = None) -> int:
    """Добавляет недостающих демо-студентов (до count штук). Возвращает, сколько добавлено."""
    total = demo_count_from_env() if count is None else max(0, min(count, 500))
    db.mark_legacy_demo_users()
    added = 0
    for i, (first, last, gender, course, specialty, nickname) in enumerate(_generated_people(total)):
        email = _demo_email(i)
        if db.get_web_user_by_email(email) is not None:
            continue
        db.create_web_user(first, last, _demo_phone(i), email,
                           password_hash="!demo-" + secrets.token_hex(16), password_salt=secrets.token_hex(16),
                           gender=gender, course=course, specialty=specialty, nickname=nickname)
        db.mark_web_email_verified(email)
        db.mark_web_user_demo(email)
        added += 1
    if added:
        logger.info("Добавлено демо-студентов: %s", added)
    return added
