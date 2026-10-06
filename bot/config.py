"""Настройки бота. Читаются из переменных окружения или файла .env в корне проекта."""
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный парсер .env: строки вида KEY=value, без перезаписи уже заданных переменных."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


@dataclass(frozen=True)
class Config:
    token: str
    admin_ids: frozenset[int]
    db_path: Path
    data_dir: Path
    bot_username: str
    reports_to_ban: int


def load_config() -> Config:
    _load_dotenv(BASE_DIR / ".env")
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN. Скопируйте .env.example в .env и вставьте токен от @BotFather.")
    admins = frozenset(int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x)
    return Config(
        token=token,
        admin_ids=admins,
        db_path=Path(os.getenv("DB_PATH", BASE_DIR / "almau.db")),
        data_dir=Path(os.getenv("DATA_DIR", BASE_DIR / "site" / "data")),
        bot_username=os.getenv("BOT_USERNAME", "almau_messenger_bot"),
        reports_to_ban=int(os.getenv("REPORTS_TO_BAN", "3")),
    )
