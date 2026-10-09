"""Точка входа: python -m bot.main"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiohttp import web

from bot.api import build_app
from bot.config import Config, load_config
from bot.content import Content
from bot.db import Database
from bot.handlers import chat, events, moderation, profile
from bot.matchmaking import Matchmaker

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Начать / профиль"),
    BotCommand(command="search", description="Найти собеседника"),
    BotCommand(command="next", description="Следующий собеседник"),
    BotCommand(command="stop", description="Завершить диалог"),
    BotCommand(command="give", description="Отправить соцсети собеседнику"),
    BotCommand(command="events", description="Афиша ивентов"),
    BotCommand(command="clubs", description="Студенческие клубы"),
    BotCommand(command="profile", description="Мой профиль"),
]


def build_dispatcher(db: Database, content: Content, config) -> Dispatcher:
    # Объекты, переданные сюда, aiogram подставляет в обработчики по имени аргумента: db, mm, content, config
    dp = Dispatcher(db=db, mm=Matchmaker(), content=content, config=config)
    dp.message.outer_middleware(moderation.BanMiddleware())
    dp.callback_query.outer_middleware(moderation.BanMiddleware())
    # Порядок важен: chat.router содержит обработчик «всего остального» и подключается последним
    dp.include_routers(moderation.router, events.router, profile.router, chat.router)
    return dp


async def run_api(bot: Bot, db: Database, content: Content, config: Config) -> None:
    """Веб-API для сайта (см. bot/api.py). Слушает $PORT, чтобы Railway видел живой HTTP-сервис."""
    app = build_app(db, content, bot=bot, admin_ids=config.admin_ids, cors_origin=config.cors_origin)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
    await site.start()
    logger.info("API слушает на порту %s", config.port)
    try:
        await asyncio.Event().wait()  # висит, пока задачу не отменят при остановке бота
    finally:
        await runner.cleanup()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    bot = Bot(config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    db, content = Database(config.db_path), Content(config.data_dir)
    dp = build_dispatcher(db, content, config)
    await bot.set_my_commands(COMMANDS)
    matcher = asyncio.create_task(chat.matchmaking_loop(bot, db, dp["mm"], content))
    api_task = asyncio.create_task(run_api(bot, db, content, config))
    try:
        await dp.start_polling(bot)
    finally:
        matcher.cancel()
        api_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
