"""Загрузка клубов и ивентов из site/data/*.json — одни и те же данные показывают сайт и бот."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Club:
    id: str
    name: str
    emoji: str
    category: str
    description: str
    instagram: str | None
    activities: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    emoji: str
    date: str
    host: str
    category: str
    description: str


class Content:
    def __init__(self, data_dir: Path) -> None:
        clubs = json.loads((data_dir / "clubs.json").read_text(encoding="utf-8"))
        events = json.loads((data_dir / "events.json").read_text(encoding="utf-8"))
        self.clubs = [
            Club(c["id"], c["name"], c["emoji"], c["category"], c["description"],
                 c.get("instagram"), tuple(c.get("activities", [])))
            for c in clubs
        ]
        self.events = [
            Event(e["id"], e["title"], e["emoji"], e["date"], e["host"], e["category"], e["description"])
            for e in events
        ]
        self._events_by_id = {e.id: e for e in self.events}
        if len(self._events_by_id) != len(self.events):
            raise ValueError("В events.json повторяются id ивентов")

    def event(self, event_id: str) -> Event | None:
        return self._events_by_id.get(event_id)
