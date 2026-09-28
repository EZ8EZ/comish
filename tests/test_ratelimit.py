from commish.ratelimit import RateLimiter, SlidingWindow


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_sliding_window_expires():
    clock = Clock()
    window = SlidingWindow(limit=2, window_s=10, clock=clock)
    assert window.allow("k") and window.allow("k")
    assert not window.allow("k")
    clock.t = 10
    assert window.allow("k")


def test_limits_are_per_sender():
    limiter = RateLimiter(per_sender_per_10min=1, outbound_per_day=10, clock=Clock())
    assert limiter.allow_question("+1a")
    assert not limiter.allow_question("+1a")
    assert limiter.allow_question("+1b")
