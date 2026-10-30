from datetime import UTC, datetime, timedelta

import pytest

from rate_limiter import Decision, RateLimiter

T0 = datetime(2026, 1, 1, tzinfo=UTC)
OLD = T0 - timedelta(days=30)
MICRO = timedelta(microseconds=1)
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
