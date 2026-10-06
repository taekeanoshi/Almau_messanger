from bot.config import BASE_DIR
from bot.content import Content


def test_site_data_is_valid_for_bot():
    content = Content(BASE_DIR / "site" / "data")
    assert len(content.clubs) >= 10 and len(content.events) >= 10
    for ev in content.events:
        # id попадает в callback_data (лимит Telegram 64 байта) и в ссылку ?start=event_<id>
        assert ev.id.replace("-", "").isalnum() and len(f"buddy:{ev.id}".encode()) <= 64
        assert len(f"event_{ev.id}") <= 64
    assert content.event("halloween").host == "Lumos"
