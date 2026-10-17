from api import ratelimit
from api.ratelimit import RateLimiter


def test_requests_without_key_are_limited_per_ip(monkeypatch):
    monkeypatch.setattr(ratelimit, "PER_IP_WITHOUT_KEY", 3)
    limiter = RateLimiter()
    assert all(limiter.check("1.2.3.4", None, now=0) for _ in range(3))
    assert not limiter.check("1.2.3.4", None, now=1)
    assert limiter.check("5.6.7.8", None, now=1)


def test_window_slides(monkeypatch):
    monkeypatch.setattr(ratelimit, "PER_KEY", 2)
    limiter = RateLimiter()
    assert limiter.check("ip", "key-a", now=0)
    assert limiter.check("ip", "key-a", now=1)
    assert not limiter.check("ip", "key-a", now=2)
    assert limiter.check("ip", "key-a", now=61)


def test_rotating_tokens_still_hits_the_ip_limit(monkeypatch):
    monkeypatch.setattr(ratelimit, "PER_IP", 5)
    limiter = RateLimiter()
    results = [limiter.check("9.9.9.9", f"fake-{i}", now=0) for i in range(6)]
    assert results == [True] * 5 + [False]


def test_api_answers_429_when_over_the_limit(client, monkeypatch):
    monkeypatch.setattr(ratelimit, "PER_IP_WITHOUT_KEY", 2)
    client.app.state.rate_limiter = RateLimiter()
    statuses = [client.get("/me").status_code for _ in range(2)] + [client.get("/health").status_code]
    assert statuses == [401, 401, 429]
