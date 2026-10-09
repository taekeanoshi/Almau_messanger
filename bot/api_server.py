"""Отдельная точка входа: запускает только веб-API, без Telegram-бота.

Используется, когда бота хостит кто-то другой из команды — этому процессу не нужен
BOT_TOKEN, он не стучится в Telegram и не зависит от того, где и как задеплоен бот.
У него своя БД (заявки с сайта и отметки «Иду» через веб-кнопку).

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

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(BASE_DIR / ".env")

    db_path = Path(os.getenv("DB_PATH", BASE_DIR / "almau_api.db"))
    data_dir = Path(os.getenv("DATA_DIR", BASE_DIR / "site" / "data"))
    port = int(os.getenv("PORT", "8080"))
    cors_origin = os.getenv("CORS_ORIGIN", "*")
    admin_token = os.getenv("ADMIN_TOKEN", "")

    db, content = Database(db_path), Content(data_dir)
    app = build_app(db, content, bot=None, admin_ids=frozenset(), cors_origin=cors_origin,
                     admin_token=admin_token)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info("API слушает на порту %s (без Telegram-бота)", port)
    if not admin_token:
        logger.warning("ADMIN_TOKEN не задан — GET /api/leads будет недоступен")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
