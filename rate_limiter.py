"""Ограничитель частоты сообщений: лимит на чат и общий лимит пользователя."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Limits:
    """Лимиты одного уровня: сколько сообщений пропускать за окно."""

    per_chat: int
    chat_window: timedelta
    total: int
    total_window: timedelta


REGULAR_LIMITS = Limits(5, timedelta(seconds=10), 20, timedelta(minutes=1))


@dataclass(frozen=True)
class Decision:
    """Решение по сообщению.

    При отказе retry_after - секунды до первого момента, когда отправка пройдёт,
    если пользователь больше ничего не отправит. При пропуске retry_after равен 0.0.
    """

    allowed: bool
    retry_after: float


class RateLimiter:
    """Решает, можно ли пользователю отправить сообщение, и засчитывает пропущенное.

    Время приходит только параметром now, системные часы не читаются.
    Не потокобезопасен.
    """

    def __init__(self, regular: Limits = REGULAR_LIMITS) -> None:
        self._regular = regular
        self._logs: dict[str, deque[tuple[datetime, str]]] = {}

    def try_acquire(
        self,
        user_id: str,
        chat_id: str,
        *,
        account_created_at: datetime,
        now: datetime,
    ) -> Decision:
        """Пропускает сообщение и засчитывает его либо отказывает с подсказкой.

        Отказанное сообщение в лимит не засчитывается.
        """
        log = self._logs.get(user_id, deque())
        wait = _wait_under(self._regular, log, chat_id, now)
        if wait > timedelta(0):
            return Decision(False, wait.total_seconds())
        log.append((now, chat_id))
        self._logs[user_id] = log
        return Decision(True, 0.0)


def _wait_under(
    limits: Limits, log: deque[tuple[datetime, str]], chat_id: str, now: datetime
) -> timedelta:
    # Граница окна не входит: запись из момента t перестаёт считаться ровно в t + window.
    chat_times = [
        t for t, chat in log if chat == chat_id and t > now - limits.chat_window
    ]
    return _wait_for(chat_times, limits.per_chat, limits.chat_window, now)


def _wait_for(
    times: list[datetime], limit: int, window: timedelta, now: datetime
) -> timedelta:
    if len(times) < limit:
        return timedelta(0)
    # Отправка пройдёт, когда из окна выпадет эта запись и в нём останется limit - 1.
    return times[-limit] + window - now
