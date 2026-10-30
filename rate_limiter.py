"""Ограничитель частоты сообщений: лимит на чат и общий лимит пользователя."""

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Limits:
    """Лимиты уровня: per_chat за chat_window в чате, total за total_window всего."""

    per_chat: int
    chat_window: timedelta
    total: int
    total_window: timedelta

    def __post_init__(self) -> None:
        if self.per_chat < 1 or self.total < 1:
            raise ValueError("per_chat and total must be at least 1")
        if self.chat_window <= timedelta(0) or self.total_window <= timedelta(0):
            raise ValueError("chat_window and total_window must be positive")


REGULAR_LIMITS = Limits(5, timedelta(seconds=10), 20, timedelta(minutes=1))
NEW_ACCOUNT_LIMITS = Limits(2, timedelta(seconds=10), 10, timedelta(minutes=1))
NEW_ACCOUNT_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class Decision:
    """Решение по сообщению.

    При отказе retry_after - секунды до первого момента, когда отправка пройдёт,
    если пользователь больше ничего не отправит. При пропуске равен 0.0.
    """

    allowed: bool
    retry_after: float


class RateLimiter:
    """Решает, можно ли отправить сообщение, и засчитывает пропущенное.

    Системные часы не читаются: now передаёт вызывающий, с часов сервера и прямо
    в момент вызова. На этом держатся самоочистка журналов и проверка порядка
    времени. Не потокобезопасен.
    """

    def __init__(
        self,
        regular: Limits = REGULAR_LIMITS,
        new_account: Limits = NEW_ACCOUNT_LIMITS,
        new_account_age: timedelta = NEW_ACCOUNT_AGE,
    ) -> None:
        if new_account_age < timedelta(0):
            raise ValueError("new_account_age must not be negative")
        self._regular = regular
        self._new_account = new_account
        self._new_account_age = new_account_age
        # Журнал за самое длинное окно обоих уровней целиком покрывает и окно чата.
        self._retention = max(
            regular.chat_window,
            regular.total_window,
            new_account.chat_window,
            new_account.total_window,
        )
        self._logs: OrderedDict[str, deque[tuple[datetime, str]]] = OrderedDict()

    def try_acquire(
        self,
        user_id: str,
        chat_id: str,
        *,
        account_created_at: datetime,
        now: datetime,
    ) -> Decision:
        """Пропускает и засчитывает сообщение либо отказывает с подсказкой.

        Отказанное в лимит не засчитывается. Бросает ValueError, если now раньше
        последнего принятого сообщения этого пользователя.
        """
        now = _to_utc(now)
        account_created_at = _to_utc(account_created_at)
        known = self._logs.get(user_id)
        if known and now < known[-1][0]:
            raise ValueError(
                "now is earlier than the last accepted message of this user"
            )
        cutoff = now - self._retention
        # Журналы идут по порядку последней записи, поэтому истёкшие лежат в голове.
        while self._logs and next(iter(self._logs.values()))[-1][0] <= cutoff:
            self._logs.popitem(last=False)
        log = self._logs.get(user_id, deque())
        while log and log[0][0] <= cutoff:
            log.popleft()
        wait = self._wait(log, chat_id, account_created_at, now)
        if wait > timedelta(0):
            return Decision(False, wait.total_seconds())
        log.append((now, chat_id))
        self._logs[user_id] = log
        self._logs.move_to_end(user_id)
        return Decision(True, 0.0)

    def _wait(
        self,
        log: deque[tuple[datetime, str]],
        chat_id: str,
        account_created_at: datetime,
        now: datetime,
    ) -> timedelta:
        becomes_regular_at = account_created_at + self._new_account_age
        if now >= becomes_regular_at:
            return _wait_under(self._regular, log, chat_id, now)
        wait = _wait_under(self._new_account, log, chat_id, now)
        if now + wait < becomes_regular_at:
            return wait
        # Строгое ожидание выходит за переход через 24 ч: дальше считаем по обычным.
        return max(
            becomes_regular_at - now, _wait_under(self._regular, log, chat_id, now)
        )


def _to_utc(moment: datetime) -> datetime:
    # С одним tzinfo Python сравняет по настенным часам: на переводе часов окна врут.
    return moment if moment.utcoffset() is None else moment.astimezone(UTC)


def _wait_under(
    limits: Limits, log: deque[tuple[datetime, str]], chat_id: str, now: datetime
) -> timedelta:
    # Граница не входит: запись из момента t считается, пока now - t < window.
    chat_times = [
        t for t, chat in log if chat == chat_id and t > now - limits.chat_window
    ]
    total_times = [t for t, _ in log if t > now - limits.total_window]
    return max(
        _wait_for(chat_times, limits.per_chat, limits.chat_window, now),
        _wait_for(total_times, limits.total, limits.total_window, now),
    )


def _wait_for(
    times: list[datetime], limit: int, window: timedelta, now: datetime
) -> timedelta:
    if len(times) < limit:
        return timedelta(0)
    # Отправка пройдёт, когда из окна выпадет эта запись и в нём останется limit - 1.
    return times[-limit] + window - now
