import pytest

from bot.matchmaking import Matchmaker

IT, SPORT, KINO = frozenset({"IT"}), frozenset({"Спорт"}), frozenset({"Кино"})


def test_first_user_waits_second_gets_paired():
    mm = Matchmaker()
    assert mm.enqueue(1, now=0) is None
    assert mm.is_waiting(1)
    assert mm.enqueue(2, now=1) == 1
    assert mm.partner_of(1) == 2 and mm.partner_of(2) == 1
    assert mm.waiting_count == 0 and mm.active_pairs == 1


def test_waits_for_common_interests_then_prefers_them():
    mm = Matchmaker(patience=15)
    assert mm.enqueue(1, SPORT, now=0) is None
    assert mm.enqueue(2, IT | KINO, now=1) is None   # общих тем с 1 нет — оба ждут
    assert mm.enqueue(3, IT, now=2) == 2             # у 3 и 2 общий IT
    assert mm.is_waiting(1)


def test_user_without_interests_matches_anyone_at_once():
    mm = Matchmaker()
    mm.enqueue(1, SPORT, now=0)
    assert mm.enqueue(2, frozenset(), now=1) == 1


def test_long_waiter_is_taken_by_newcomer():
    mm = Matchmaker(patience=15)
    mm.enqueue(1, SPORT, now=0)
    assert mm.enqueue(2, IT, now=20) == 1


def test_pair_stale_matches_after_patience():
    mm = Matchmaker(patience=15)
    mm.enqueue(1, SPORT, now=0)
    mm.enqueue(2, IT, now=5)
    assert mm.pair_stale(now=10) == []
    assert mm.pair_stale(now=16) == [(1, 2, None)]
    assert mm.partner_of(2) == 1 and mm.waiting_count == 0


def test_event_search_is_separate_from_general():
    mm = Matchmaker()
    mm.enqueue(1, now=0)
    assert mm.enqueue(2, event_id="halloween", now=0) is None
    assert mm.enqueue(3, event_id="almaucup", now=0) is None
    assert mm.enqueue(4, event_id="halloween", now=0) == 2
    assert mm.pair_stale(now=100) == []  # 1 и 3 ищут в разных очередях


def test_next_avoids_last_partner_when_possible():
    mm = Matchmaker()
    mm.enqueue(1, now=0)
    mm.enqueue(2, now=0)
    mm.end_dialog(1)
    mm.enqueue(2, SPORT, now=1)
    mm.enqueue(3, IT, now=2)          # 2 и 3 без общих тем — оба ждут
    assert mm.enqueue(1, now=3) == 3  # 2 — прошлый собеседник, пропускаем


def test_last_partner_allowed_if_nobody_else():
    mm = Matchmaker()
    mm.enqueue(1, now=0)
    mm.enqueue(2, now=0)
    mm.end_dialog(2)
    mm.enqueue(1, now=1)
    assert mm.enqueue(2, now=2) == 1


def test_end_dialog_and_queue_cleanup():
    mm = Matchmaker()
    mm.enqueue(1, now=0)
    mm.enqueue(2, now=0)
    assert mm.end_dialog(2) == 1
    assert mm.partner_of(1) is None
    assert mm.end_dialog(2) is None
    mm.enqueue(5, now=0)
    assert mm.leave_queue(5) and not mm.leave_queue(5)


def test_cannot_search_while_in_dialog():
    mm = Matchmaker()
    mm.enqueue(1, now=0)
    mm.enqueue(2, now=0)
    with pytest.raises(ValueError):
        mm.enqueue(1, now=0)


def test_requeue_does_not_duplicate():
    mm = Matchmaker()
    mm.enqueue(1, now=0)
    mm.enqueue(1, event_id="x", now=0)
    assert mm.waiting_count == 1
    assert mm.enqueue(2, now=0) is None  # 1 теперь ищет только по ивенту
