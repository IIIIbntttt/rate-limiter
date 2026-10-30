from datetime import UTC, datetime, timedelta

import pytest

from rate_limiter import Decision, Limits, RateLimiter

T0 = datetime(2026, 1, 1, tzinfo=UTC)
OLD = T0 - timedelta(days=30)
MICRO = timedelta(microseconds=1)
HOUR = timedelta(hours=1)
DAY = timedelta(hours=24)
ALLOWED = Decision(True, 0.0)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def send(limiter, chat, now, user="alice", created=OLD):
    return limiter.try_acquire(user, chat, account_created_at=created, now=now)


def test_allows_five_messages_in_chat_within_ten_seconds():
    limiter = RateLimiter()

    assert [send(limiter, "chat", at(t)) for t in range(5)] == [ALLOWED] * 5


def test_rejects_sixth_message_in_chat_with_exact_wait():
    limiter = RateLimiter()
    for t in range(5):
        send(limiter, "chat", at(t))

    assert send(limiter, "chat", at(5)) == Decision(False, 5.0)


def test_sixth_message_exactly_ten_seconds_after_first_is_allowed():
    limiter = RateLimiter()
    for t in range(5):
        send(limiter, "chat", at(t))

    assert send(limiter, "chat", at(10)) == ALLOWED


def test_retry_after_points_to_first_moment_sending_is_allowed():
    limiter = RateLimiter()
    for t in range(5):
        send(limiter, "chat", at(t))
    retry_after = send(limiter, "chat", at(5)).retry_after
    retry_at = at(5) + timedelta(seconds=retry_after)

    assert send(limiter, "chat", retry_at - MICRO) == Decision(
        False, MICRO.total_seconds()
    )
    assert send(limiter, "chat", retry_at) == ALLOWED


def test_messages_at_same_moment_count_separately():
    limiter = RateLimiter()
    for _ in range(5):
        send(limiter, "chat", at(0))

    assert send(limiter, "chat", at(0)) == Decision(False, 10.0)


def test_chats_are_limited_separately():
    limiter = RateLimiter()
    for t in range(5):
        send(limiter, "chat-a", at(t))

    assert send(limiter, "chat-b", at(5)) == ALLOWED


def test_twenty_first_message_across_five_chats_is_rejected_by_total_limit():
    limiter = RateLimiter()
    for i in range(20):
        assert (
            send(limiter, f"chat-{i // 5}", at(10 * (i // 5) + 2 * (i % 5))) == ALLOWED
        )

    assert send(limiter, "chat-4", at(40)) == Decision(False, 20.0)


@pytest.mark.parametrize(
    ("burst_start", "attempt", "expected_wait"),
    [
        (30, 35, 25.0),  # общий лимит ждёт дольше
        (55, 59.5, 5.5),  # лимит чата ждёт дольше
    ],
)
def test_when_both_limits_hit_returns_longer_wait(burst_start, attempt, expected_wait):
    limiter = RateLimiter()
    for chat, start in (("a", 0), ("b", 5), ("c", 10)):
        for t in range(start, start + 5):
            send(limiter, chat, at(t))
    for t in range(burst_start, burst_start + 5):
        send(limiter, "x", at(t))

    assert send(limiter, "x", at(attempt)) == Decision(False, expected_wait)


def test_new_account_allows_two_messages_per_chat_in_ten_seconds():
    limiter = RateLimiter()
    created = T0 - HOUR

    assert send(limiter, "chat", at(0), created=created) == ALLOWED
    assert send(limiter, "chat", at(1), created=created) == ALLOWED
    assert send(limiter, "chat", at(2), created=created) == Decision(False, 8.0)


def test_new_account_allows_ten_messages_in_total_per_minute():
    limiter = RateLimiter()
    created = T0 - HOUR
    for i in range(10):
        assert send(limiter, f"chat-{i // 2}", at(i), created=created) == ALLOWED

    assert send(limiter, "chat-5", at(10), created=created) == Decision(False, 50.0)


def test_regular_limits_apply_from_moment_account_turns_24_hours():
    limiter = RateLimiter()
    created = at(5) - DAY

    assert send(limiter, "chat", at(0), created=created) == ALLOWED
    assert send(limiter, "chat", at(1), created=created) == ALLOWED
    assert send(limiter, "chat", at(2), created=created) == Decision(False, 3.0)
    for t in (5, 6, 7):
        assert send(limiter, "chat", at(t), created=created) == ALLOWED

    assert send(limiter, "chat", at(8), created=created) == Decision(False, 2.0)


def test_wait_accounts_for_account_turning_24_hours():
    limiter = RateLimiter()
    created = at(5) - DAY
    send(limiter, "chat", at(-1), created=created)
    send(limiter, "chat", at(0), created=created)

    assert send(limiter, "chat", at(0), created=created) == Decision(False, 5.0)
    assert send(limiter, "chat", at(5) - MICRO, created=created) == Decision(
        False, MICRO.total_seconds()
    )
    assert send(limiter, "chat", at(5), created=created) == ALLOWED


def test_wait_after_turning_24_hours_respects_stricter_regular_limits():
    limiter = RateLimiter(
        regular=Limits(2, timedelta(seconds=10), 10, timedelta(seconds=60)),
        new_account=Limits(5, timedelta(seconds=10), 20, timedelta(seconds=60)),
    )
    created = at(5) - DAY
    for t in range(5):
        assert send(limiter, "chat", at(t), created=created) == ALLOWED

    assert send(limiter, "chat", at(4.5), created=created) == Decision(False, 8.5)
    assert send(limiter, "chat", at(13) - MICRO, created=created) == Decision(
        False, MICRO.total_seconds()
    )
    assert send(limiter, "chat", at(13), created=created) == ALLOWED


def test_account_created_in_future_gets_new_account_limits():
    limiter = RateLimiter()
    created = at(100)

    assert send(limiter, "chat", at(0), created=created) == ALLOWED
    assert send(limiter, "chat", at(1), created=created) == ALLOWED
    assert send(limiter, "chat", at(2), created=created) == Decision(False, 8.0)
