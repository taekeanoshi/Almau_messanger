"""Запуск бота «по-настоящему»: python -m bot.main целиком, с polling, против поддельного сервера Telegram.

Обычные тесты подают обновления прямо в диспетчер. Этот проверяет весь путь запуска:
токен, getMe, webhook, команды, polling, ответ на /start и /health.
"""
import asyncio
import socket
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot import main as bot_main
from bot.handlers import chat, club_admin, events, hikes, moderation, profile

USER = 5


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_telegram(sent: list, update_text: str = "/start"):
    """Минимальный Bot API: getMe, getUpdates (одно сообщение от пользователя), sendMessage и остальное."""
    delivered = {"done": False}

    async def handler(request: web.Request) -> web.Response:
        method = request.match_info["method"]
        form = await request.post()
        if method == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        elif method == "getUpdates":
            if delivered["done"]:
                await asyncio.sleep(0.2)
                result = []
            else:
                delivered["done"] = True
                result = [{"update_id": 1, "message": {
                    "message_id": 1, "date": int(time.time()), "chat": {"id": USER, "type": "private"},
                    "from": {"id": USER, "is_bot": False, "first_name": "Айдар"},
                    "text": update_text, "entities": [{"type": "bot_command", "offset": 0, "length": len(update_text)}]}}]
        elif method == "sendMessage":
            sent.append((int(form["chat_id"]), form["text"]))
            result = {"message_id": len(sent), "date": int(time.time()),
                      "chat": {"id": int(form["chat_id"]), "type": "private"}, "text": form["text"]}
        else:
            result = True
        return web.json_response({"ok": True, "result": result})

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handler)
    return app


def test_bot_starts_polls_and_answers_start(monkeypatch, tmp_path):
    sent: list = []
    api_port = free_port()
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setenv("PORT", str(api_port))
    monkeypatch.setenv("ADMIN_IDS", "900")
    for module in (chat, club_admin, events, hikes, moderation, profile):   # роутеры модульные
        module.router._parent_router = None

    async def scenario():
        server = TestServer(fake_telegram(sent))
        await server.start_server()
        monkeypatch.setenv("TELEGRAM_API_URL", f"http://127.0.0.1:{server.port}")
        task = asyncio.create_task(bot_main.main())
        try:
            for _ in range(100):
                if any(chat_id == USER for chat_id, _ in sent):
                    break
                if task.done():
                    task.result()
                await asyncio.sleep(0.1)
            # API живёт в том же процессе и сообщает, что бот запущен
            import aiohttp
            async with aiohttp.ClientSession() as http:
                async with http.get(f"http://127.0.0.1:{api_port}/health") as resp:
                    health = await resp.json()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await server.close()
        return health

    health = asyncio.run(scenario())
    replies = [text for chat_id, text in sent if chat_id == USER]
    assert replies and "Где ты учишься" in replies[0], f"бот не ответил на /start, отправлено: {sent}"
    assert any(chat_id == 900 and "запущен" in text for chat_id, text in sent)   # админ получил «бот запущен»
    assert health == {"status": "ok", "mode": "bot+api"}
