"""Демо-студенты для вкладки «Люди» и прямого чата: 10 аккаунтов с казахскими именами.

База SQLite на Railway сбрасывается при каждом деплое (см. README, «Хостинг»), поэтому
людей не вставляем руками один раз, а досеиваем при каждом старте сервиса — как походы
из site/data/hikes.json. Сид идемпотентный: аккаунт с такой почтой уже есть — пропускаем.

Это витрина, а не настоящие люди: почты на зарезервированном домене example.com (письма
туда не уходят), телефоны фиктивные, а пароль заведомо невалидный — войти под ними
нельзя, ни один хеш PBKDF2 не начинается с «!». Отключить сид: SEED_DEMO_PEOPLE=0.
"""
import logging
import os
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


def _demo_email(index: int) -> str:
    return f"demo{index + 1:02d}@example.com"


def _demo_phone(index: int) -> str:
    return f"+7700000{index + 1:04d}"


def seed_demo_people(db: Database) -> int:
    """Добавляет недостающих демо-студентов. Возвращает, сколько добавлено."""
    if os.getenv("SEED_DEMO_PEOPLE", "1").strip() == "0":
        return 0
    added = 0
    for i, (first, last, gender, course, specialty, nickname) in enumerate(DEMO_PEOPLE):
        email = _demo_email(i)
        if db.get_web_user_by_email(email) is not None:
            continue
        db.create_web_user(first, last, _demo_phone(i), email,
                           password_hash="!demo-" + secrets.token_hex(16), password_salt=secrets.token_hex(16),
                           gender=gender, course=course, specialty=specialty, nickname=nickname)
        db.mark_web_email_verified(email)
        added += 1
    if added:
        logger.info("Добавлено демо-студентов: %s", added)
    return added
