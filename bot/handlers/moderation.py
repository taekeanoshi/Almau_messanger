"""Модерация: жалобы с автоблокировкой, блокировка забаненных и команды администратора."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot import keyboards as kb
from bot.config import Config
from bot.db import Database
from bot.handlers.chat import end_dialog
from bot.matchmaking import Matchmaker

router = Router(name="moderation")


class IsAdmin(Filter):
    async def __call__(self, message: Message, config: Config) -> bool:
        return message.from_user.id in config.admin_ids


class BanMiddleware(BaseMiddleware):
    """Не пускает заблокированных пользователей ни к одному обработчику."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        db: Database = data["db"]
        user = data.get("event_from_user")
        if user and db.is_banned(user.id) and user.id not in data["config"].admin_ids:
            if isinstance(event, Message):
                await event.answer("⛔ Ты заблокирован за нарушение правил.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Ты заблокирован", show_alert=True)
            return None
        return await handler(event, data)


async def _notify_admins(bot: Bot, config: Config, text: str) -> None:
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # админ мог не запускать бота — модерация не должна из-за этого падать
            pass


@router.message(F.text == kb.BTN_REPORT)
async def on_report(message: Message, db: Database, mm: Matchmaker, config: Config) -> None:
    reporter = message.from_user.id
    target = await end_dialog(message.bot, mm, reporter, "Собеседник завершил диалог.")
    if target is None:
        await message.answer("Жаловаться можно только во время диалога.", reply_markup=kb.main_menu)
        return

    count = db.add_report(reporter, target)
    await message.answer("Спасибо, жалоба отправлена модератору. Диалог завершён.", reply_markup=kb.main_menu)
    await _notify_admins(message.bot, config,
                         f"🚩 Жалоба на <code>{target}</code> (всего разных жалоб: {count}).\n"
                         f"Заблокировать: /ban {target}")

    if count >= config.reports_to_ban and not db.is_banned(target):
        db.set_banned(target, True)
        await _notify_admins(message.bot, config,
                             f"🤖 <code>{target}</code> заблокирован автоматически ({count} жалоб). "
                             f"Разблокировать: /unban {target}")


@router.message(Command("ban"), IsAdmin())
async def on_ban(message: Message, command: CommandObject, db: Database, mm: Matchmaker) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /ban <id>")
        return
    target = int(command.args)
    if not db.set_banned(target, True):
        await message.answer("Пользователь не найден.")
        return
    await end_dialog(message.bot, mm, target, "Собеседник завершил диалог.")
    mm.leave_queue(target)
    await message.answer(f"Пользователь {target} заблокирован.")


@router.message(Command("unban"), IsAdmin())
async def on_unban(message: Message, command: CommandObject, db: Database) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /unban <id>")
        return
    ok = db.set_banned(int(command.args), False)
    await message.answer("Разблокирован." if ok else "Пользователь не найден.")


@router.message(Command("stats"), IsAdmin())
async def on_stats(message: Message, db: Database, mm: Matchmaker) -> None:
    s = db.stats()
    await message.answer(
        "📊 <b>Статистика</b>\n"
        f"Пользователей: {s['users']} (заблокировано: {s['banned']})\n"
        f"Диалогов всего: {s['dialogs_total']}, сегодня: {s['dialogs_today']}\n"
        f"Из них через ивенты: {s['event_dialogs']}\n"
        f"Жалоб: {s['reports']}\n"
        f"Заявок с сайта: {s['leads']}\n"
        f"Сейчас общаются: {mm.active_pairs} пар, в поиске: {mm.waiting_count}"
    )


@router.message(Command("leads"), IsAdmin())
async def on_leads(message: Message, db: Database) -> None:
    rows = db.recent_leads(10)
    if not rows:
        await message.answer("Заявок с сайта пока нет.")
        return
    lines = ["📝 <b>Заявки с сайта</b> (последние 10)"]
    for r in rows:
        extra = f" · ивент {r['event_id']}" if r["event_id"] else ""
        text = f" — {r['message']}" if r["message"] else ""
        lines.append(f"{r['created_at']} · {r['name']} ({r['contact']}){extra}{text}")
    await message.answer("\n".join(lines))
