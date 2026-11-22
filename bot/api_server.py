"""Точка входа сервера Pople: запускает веб-API сайта.

Запуск: python -m bot.api_server
"""
import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web

from bot.api import build_app
from bot.config import BASE_DIR, _load_dotenv
from bot.content import Content
from bot.db import Database
from bot.curriculum import spread_demo_activity, sync_all
from bot.demo_people import seed_demo_people

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(BASE_DIR / ".env")

    db_path = Path(os.getenv("DB_PATH", BASE_DIR / "almau_api.db"))
    data_dir = Path(os.getenv("DATA_DIR", BASE_DIR / "site" / "data"))
    port = int(os.getenv("PORT", "8080"))
    cors_origin = os.getenv("CORS_ORIGIN", "*")
    admin_token = os.getenv("ADMIN_TOKEN", "")
    brevo_api_key = os.getenv("BREVO_API_KEY", "")
    brevo_sender_email = os.getenv("BREVO_SENDER_EMAIL", "")
    brevo_sender_name = os.getenv("BREVO_SENDER_NAME", "Pople")
    reports_to_ban = int(os.getenv("REPORTS_TO_BAN", "3"))

    db, content = Database(db_path), Content(data_dir)
    db.hikes.seed(content.hikes_seed)
    seed_demo_people(db)
    sync_all(db)                 # каталог дисциплин AlmaU, записи студентов, текущие оценки демо-студентов
    spread_demo_activity(db)     # «был(а) в сети» у демо-аккаунтов — в пределах последних трёх суток
    app = build_app(db, content, cors_origin=cors_origin,
                     admin_token=admin_token, brevo_api_key=brevo_api_key,
                     brevo_sender_email=brevo_sender_email, brevo_sender_name=brevo_sender_name,
                     reports_to_ban=reports_to_ban)
    if not brevo_api_key or not brevo_sender_email:
        logger.warning("BREVO_API_KEY/BREVO_SENDER_EMAIL не заданы — коды входа будут только в логах, письма не пойдут")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("API слушает на порту %s", port)
    if not admin_token:
        logger.warning("ADMIN_TOKEN не задан — эндпоинты /api/admin/* с серверным токеном недоступны")
    if cors_origin == "*":
        logger.warning("CORS_ORIGIN=* — API принимает запросы с любых сайтов. "
                       "Укажите домен сайта (например https://almau-messenger.vercel.app)")
    while True:                  # кто-то из демо-студентов всегда «в сети»
        await asyncio.sleep(180)
        try:
            spread_demo_activity(db, share=0.04, within_minutes=3)
        except Exception:
            logger.exception("Не получилось обновить активность демо-аккаунтов")


if __name__ == "__main__":
    asyncio.run(main())
