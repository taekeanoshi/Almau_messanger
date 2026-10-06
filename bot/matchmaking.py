"""Очередь поиска и активные пары собеседников.

Подбор по интересам: если у ожидающего и нового пользователя нет общих интересов,
новый тоже встаёт в очередь и до PATIENCE секунд ждёт человека с общими темами.
Кто прождал дольше, того фоновая задача (pair_stale) соединяет с любым свободным.

Хранится в памяти процесса: после перезапуска бота все диалоги завершаются,
а профили пользователей остаются в базе данных.
"""
import time
from dataclasses import dataclass

PATIENCE = 15.0  # секунд ожидания собеседника с общими интересами


@dataclass(frozen=True)
class Waiting:
    user_id: int
    interests: frozenset[str]
    event_id: str | None
    since: float


class Matchmaker:
    def __init__(self, patience: float = PATIENCE) -> None:
        self.patience = patience
        self._queue: list[Waiting] = []          # в порядке прихода, первый ждёт дольше всех
        self._pairs: dict[int, int] = {}         # user_id -> partner_id, хранится в обе стороны
        self._last_partner: dict[int, int] = {}  # чтобы «Следующий» не вернул того же человека

    def partner_of(self, user_id: int) -> int | None:
        return self._pairs.get(user_id)

    def is_waiting(self, user_id: int) -> bool:
        return any(w.user_id == user_id for w in self._queue)

    @property
    def active_pairs(self) -> int:
        return len(self._pairs) // 2

    @property
    def waiting_count(self) -> int:
        return len(self._queue)

    def _good_enough(self, a: Waiting, b: Waiting, now: float) -> bool:
        """Можно ли соединить сразу: общие интересы, у кого-то их нет, или кто-то устал ждать."""
        return (bool(a.interests & b.interests) or not a.interests or not b.interests
                or now - a.since >= self.patience or now - b.since >= self.patience)

    def _best_partner(self, me: Waiting, now: float, only_good: bool) -> Waiting | None:
        candidates = [w for w in self._queue if w.event_id == me.event_id and w.user_id != me.user_id]
        last = self._last_partner.get(me.user_id)
        fresh = [w for w in candidates if w.user_id != last]
        candidates = fresh or candidates  # прошлого собеседника берём, только если больше никого нет
        if only_good:
            candidates = [w for w in candidates if self._good_enough(me, w, now)]
        if not candidates:
            return None
        # max() возвращает первый из равных, а очередь отсортирована по времени ожидания
        return max(candidates, key=lambda w: len(w.interests & me.interests))

    def _pair(self, a: int, b: int) -> None:
        self._queue = [w for w in self._queue if w.user_id not in (a, b)]
        self._pairs[a] = b
        self._pairs[b] = a

    def enqueue(self, user_id: int, interests: frozenset[str] = frozenset(),
                event_id: str | None = None, now: float | None = None) -> int | None:
        """Ставит пользователя в поиск. Возвращает id собеседника, если пара нашлась сразу.

        Подходят только ожидающие с тем же event_id (None — обычный поиск).
        """
        if user_id in self._pairs:
            raise ValueError("Пользователь уже в диалоге")
        now = time.monotonic() if now is None else now
        self.leave_queue(user_id)
        me = Waiting(user_id, interests, event_id, now)
        best = self._best_partner(me, now, only_good=True)
        if best is None:
            self._queue.append(me)
            return None
        self._pair(user_id, best.user_id)
        return best.user_id

    def pair_stale(self, now: float | None = None) -> list[tuple[int, int, str | None]]:
        """Соединяет тех, кто ждёт дольше PATIENCE, с лучшим доступным собеседником.

        Вызывается фоновой задачей каждые несколько секунд. Возвращает новые пары (a, b, event_id).
        """
        now = time.monotonic() if now is None else now
        pairs = []
        for w in list(self._queue):
            if now - w.since < self.patience or not self.is_waiting(w.user_id):
                continue
            best = self._best_partner(w, now, only_good=False)
            if best is not None:
                self._pair(w.user_id, best.user_id)
                pairs.append((w.user_id, best.user_id, w.event_id))
        return pairs

    def leave_queue(self, user_id: int) -> bool:
        before = len(self._queue)
        self._queue = [w for w in self._queue if w.user_id != user_id]
        return len(self._queue) != before

    def end_dialog(self, user_id: int) -> int | None:
        """Разрывает пару. Возвращает id бывшего собеседника или None, если диалога не было."""
        partner = self._pairs.pop(user_id, None)
        if partner is not None:
            self._pairs.pop(partner, None)
            self._last_partner[user_id] = partner
            self._last_partner[partner] = user_id
        return partner
