"""Точка входа: python -m bot.main"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import load_config
from bot.content import Content
from bot.db import Database
from bot.handlers import chat, events, moderation, profile
from bot.matchmaking import Matchmaker

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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    bot = Bot(config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    db, content = Database(config.db_path), Content(config.data_dir)
    dp = build_dispatcher(db, content, config)
    await bot.set_my_commands(COMMANDS)
    matcher = asyncio.create_task(chat.matchmaking_loop(bot, db, dp["mm"], content))
    try:
        await dp.start_polling(bot)
    finally:
        matcher.cancel()


if __name__ == "__main__":
    asyncio.run(main())
