"""Точка входа: python -m bot.main"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeChat
from aiohttp import web

from bot.api import build_app
from bot.club_jobs import club_jobs_loop
from bot.config import Config, load_config
from bot.content import Content
from bot.db import Database
from bot.handlers import chat, club_admin, events, hikes, moderation, profile
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
    BotCommand(command="hikes", description="Походы Summit Seekers"),
    BotCommand(command="school", description="Школа туризма"),
    BotCommand(command="sos", description="Помощь во время похода"),
    BotCommand(command="profile", description="Мой профиль"),
]
ORGANIZER_COMMANDS = [
    BotCommand(command="club", description="Панель организатора клуба"),
    BotCommand(command="club_report", description="Отчёт для клуба"),
]


def build_dispatcher(db: Database, content: Content, config) -> Dispatcher:
    # Объекты, переданные сюда, aiogram подставляет в обработчики по имени аргумента: db, mm, content, config
    dp = Dispatcher(db=db, mm=Matchmaker(), content=content, config=config)
    dp.message.outer_middleware(moderation.BanMiddleware())
    dp.callback_query.outer_middleware(moderation.BanMiddleware())
    # Порядок важен: chat.router содержит обработчик «всего остального» и подключается последним
    # club_admin и hikes идут раньше profile: их /start-ссылки и формы должны сработать первыми
    dp.include_routers(moderation.router, club_admin.router, hikes.router, events.router, profile.router, chat.router)
    return dp


async def run_api(bot: Bot, db: Database, content: Content, config: Config) -> None:
    """Веб-API для сайта (см. bot/api.py). Слушает $PORT, чтобы Railway видел живой HTTP-сервис."""
    app = build_app(db, content, bot=bot, admin_ids=config.admin_ids,
                     cors_origin=config.cors_origin, admin_token=config.admin_token,
                     brevo_api_key=config.brevo_api_key, brevo_sender_email=config.brevo_sender_email,
                     brevo_sender_name=config.brevo_sender_name)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
    await site.start()
    logger.info("API слушает на порту %s", config.port)
    try:
        await asyncio.Event().wait()  # висит, пока задачу не отменят при остановке бота
    finally:
        await runner.cleanup()


async def _get_me(bot: Bot, attempts: int = 5):
    """Проверяет токен. Неверный токен: понятная ошибка и выход. Сеть моргнула: повторяем с паузой."""
    for attempt in range(attempts):
        try:
            return await bot.get_me()
        except TelegramUnauthorizedError:
            logger.error("Telegram отклонил BOT_TOKEN (Unauthorized). Проверьте токен в @BotFather и переменные Railway.")
            raise SystemExit(1)
        except TelegramNetworkError as exc:
            logger.warning("Нет связи с Telegram (%s), попытка %s из %s", exc, attempt + 1, attempts)
            await asyncio.sleep(2 ** attempt)
    logger.error("Telegram недоступен. Бот не запущен.")
    raise SystemExit(1)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    session = (AiohttpSession(api=TelegramAPIServer.from_base(config.telegram_api_url))
               if config.telegram_api_url else None)
    bot = Bot(config.token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    db, content = Database(config.db_path), Content(config.data_dir)
    db.hikes.seed(content.hikes_seed)
    dp = build_dispatcher(db, content, config)
    # API поднимаем первым: Railway проверяет /health сразу после старта, даже если Telegram ещё не ответил
    api_task = asyncio.create_task(run_api(bot, db, content, config))
    me = await _get_me(bot)
    logger.info("Бот запущен: @%s", me.username)
    # Если у бота когда-то был webhook, polling не получает обновления (в логах — «Conflict»).
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(COMMANDS)
    for oid in config.club_admin_ids | config.admin_ids:
        try:
            await bot.set_my_commands(COMMANDS + ORGANIZER_COMMANDS, scope=BotCommandScopeChat(chat_id=oid))
            await bot.send_message(oid, f"✅ Бот @{me.username} запущен.")
        except Exception:  # организатор мог ещё не писать боту
            logger.warning("Не удалось уведомить организатора %s о запуске", oid)
    matcher = asyncio.create_task(chat.matchmaking_loop(bot, db, dp["mm"], content))
    jobs = asyncio.create_task(club_jobs_loop(bot, db, config))
    try:
        await dp.start_polling(bot)
    finally:
        matcher.cancel()
        jobs.cancel()
        api_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
