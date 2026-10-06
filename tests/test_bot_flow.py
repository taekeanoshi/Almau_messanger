"""Сквозные сценарии: апдейты подаются в диспетчер, ответы бота перехватываются фейковой сессией."""
import asyncio
from datetime import datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import CopyMessage, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, MessageId, Update, User

from bot.config import BASE_DIR, Config
from bot.content import Content
from bot.db import Database
from bot.handlers import chat, events, moderation, profile
from bot.main import build_dispatcher

ADMIN = 900


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=1, date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=method.text)
        if isinstance(method, CopyMessage):
            return MessageId(message_id=2)
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self):
        pass


class Harness:
    def __init__(self):
        self.session = FakeSession()
        self.bot = Bot("123:ABC", session=self.session)
        self.db = Database(":memory:")
        config = Config("123:ABC", frozenset({ADMIN}), None, BASE_DIR / "site" / "data", "test_bot", 2)
        self.content = Content(config.data_dir)
        for module in (chat, events, moderation, profile):  # роутеры модульные: отвязываем от прошлого теста
            module.router._parent_router = None
        self.dp = build_dispatcher(self.db, self.content, config)
        self.mm = self.dp["mm"]
        self._id = 0

    def _next(self):
        self._id += 1
        return self._id

    async def send(self, uid, text):
        msg = Message(message_id=self._next(), date=datetime.now(), chat=Chat(id=uid, type="private"),
                      from_user=User(id=uid, is_bot=False, first_name="U"), text=text)
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), message=msg))

    async def click(self, uid, data):
        msg = Message(message_id=self._next(), date=datetime.now(), chat=Chat(id=uid, type="private"),
                      text="x").as_(self.bot)
        cb = CallbackQuery(id=str(self._next()), from_user=User(id=uid, is_bot=False, first_name="U"),
                           chat_instance="c", data=data, message=msg)
        await self.dp.feed_update(self.bot, Update(update_id=self._next(), callback_query=cb))

    async def register(self, uid, uni=0, course=1, interests=()):
        await self.send(uid, "/start")
        await self.click(uid, f"uni:{uni}")
        await self.click(uid, f"course:{course}")
        for i in interests:
            await self.click(uid, f"int:{i}")
        await self.click(uid, "int:done")

    def texts_to(self, uid):
        return [c.text for c in self.session.calls if isinstance(c, SendMessage) and c.chat_id == uid]

    def copies_to(self, uid):
        return [c for c in self.session.calls if isinstance(c, CopyMessage) and c.chat_id == uid]


def run(coro):
    return asyncio.run(coro)


def test_registration_search_relay_reveal_give_stop():
    async def scenario():
        h = Harness()
        await h.register(1, uni=0, course=1, interests=[2])  # AlmaU, 2 курс, IT
        await h.register(2, uni=1, course=0, interests=[2])
        u = h.db.get_user(1)
        assert (u.university, u.course, u.interests) == ("AlmaU", "2 курс", frozenset({"IT"}))

        await h.send(1, "🔍 Найти собеседника")
        await h.send(2, "/search")
        assert h.mm.partner_of(1) == 2
        assert any("Общие интересы: IT" in t for t in h.texts_to(1))

        await h.send(1, "привет")
        assert h.copies_to(2), "сообщение должно уйти собеседнику копией, без имени"

        await h.send(1, "🎓 Раскрыть вуз и курс")
        assert any("AlmaU, 2 курс" in t for t in h.texts_to(2))

        await h.send(1, "/give")
        assert any("Соцсети не указаны" in t for t in h.texts_to(1))
        await h.click(1, "prof:socials")
        await h.send(1, "inst @student")
        await h.send(1, "/give")
        assert any("inst @student" in t for t in h.texts_to(2))

        await h.send(2, "⛔ Стоп")
        assert h.mm.partner_of(1) is None
        assert any("завершил диалог" in t for t in h.texts_to(1))
    run(scenario())


def test_unregistered_user_is_asked_to_register():
    async def scenario():
        h = Harness()
        await h.send(5, "/search")
        assert any("/start" in t for t in h.texts_to(5))
        assert not h.mm.is_waiting(5)
    run(scenario())


def test_reports_auto_ban_and_admin_commands():
    async def scenario():
        h = Harness()
        for uid in (1, 2, 3):
            await h.register(uid)
        for reporter in (1, 3):  # REPORTS_TO_BAN = 2 в тестовом конфиге
            await h.send(reporter, "/search")
            await h.send(2, "/search")
            assert h.mm.partner_of(reporter) == 2
            await h.send(reporter, "🚩 Пожаловаться")
        assert h.db.is_banned(2)
        assert any("заблокирован автоматически" in t for t in h.texts_to(ADMIN))

        await h.send(2, "/search")
        assert not h.mm.is_waiting(2)
        assert any("заблокирован" in t for t in h.texts_to(2))

        await h.send(1, "/unban 2")  # не админ — команда не сработает, уйдёт в relay
        assert h.db.is_banned(2)
        await h.send(ADMIN, "/unban 2")
        assert not h.db.is_banned(2)
        await h.send(ADMIN, "/stats")
        assert any("Жалоб: 2" in t for t in h.texts_to(ADMIN))
    run(scenario())


def test_event_buddy_search_and_deep_link():
    async def scenario():
        h = Harness()
        await h.register(1)
        await h.click(1, "go:halloween")
        assert h.db.is_going(1, "halloween")
        await h.click(1, "buddy:halloween")
        assert h.mm.is_waiting(1)

        await h.register(2)
        await h.send(2, "/search")  # обычный поиск не должен попасть в очередь ивента
        assert h.mm.partner_of(1) is None
        await h.send(2, "⛔ Стоп")

        # новый пользователь приходит с сайта по ссылке ?start=event_halloween
        await h.send(3, "/start event_halloween")
        await h.click(3, "uni:0")
        await h.click(3, "course:0")
        await h.click(3, "int:done")
        assert any("Halloween by Lumos" in t for t in h.texts_to(3))
        await h.click(3, "buddy:halloween")
        assert h.mm.partner_of(3) == 1
        assert any("Вы оба идёте на «Halloween by Lumos»" in t for t in h.texts_to(1))
        assert h.db.stats()["event_dialogs"] == 1
    run(scenario())
