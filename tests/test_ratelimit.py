from __future__ import annotations

import threading
import unittest

from sahne_avcisi.ratelimit import RateLimiter, from_environment


class RateLimiterTests(unittest.TestCase):
    def test_requests_inside_the_budget_are_allowed(self) -> None:
        limiter = RateLimiter(limit=3, window_seconds=60)
        self.assertEqual([limiter.allow("a", now=100) for _ in range(3)], [True, True, True])

    def test_the_request_over_the_budget_is_refused(self) -> None:
        limiter = RateLimiter(limit=2, window_seconds=60)
        limiter.allow("a", now=100)
        limiter.allow("a", now=101)
        self.assertFalse(limiter.allow("a", now=102))

    def test_the_budget_refills_once_the_window_passes(self) -> None:
        limiter = RateLimiter(limit=1, window_seconds=10)
        self.assertTrue(limiter.allow("a", now=100))
        self.assertFalse(limiter.allow("a", now=105))
        self.assertTrue(limiter.allow("a", now=111))

    def test_callers_have_separate_budgets(self) -> None:
        limiter = RateLimiter(limit=1, window_seconds=60)
        self.assertTrue(limiter.allow("a", now=100))
        self.assertFalse(limiter.allow("a", now=100))
        self.assertTrue(limiter.allow("b", now=100))

    def test_a_zero_limit_disables_the_budget(self) -> None:
        limiter = RateLimiter(limit=0, window_seconds=60)
        self.assertFalse(limiter.enabled)
        self.assertTrue(all(limiter.allow("a", now=100) for _ in range(100)))

    def test_retry_after_reports_whole_seconds_until_the_window_clears(self) -> None:
        limiter = RateLimiter(limit=1, window_seconds=10)
        limiter.allow("a", now=100)
        self.assertEqual(limiter.retry_after("a", now=104), 6)

    def test_retry_after_is_zero_for_an_unseen_caller(self) -> None:
        self.assertEqual(RateLimiter(limit=1, window_seconds=10).retry_after("yeni"), 0)

    def test_the_budget_holds_under_concurrent_callers(self) -> None:
        limiter = RateLimiter(limit=50, window_seconds=600)
        allowed: list[bool] = []
        lock = threading.Lock()

        def hammer() -> None:
            for _ in range(25):
                result = limiter.allow("shared")
                with lock:
                    allowed.append(result)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(len(allowed), 200)
        self.assertEqual(sum(allowed), 50, "eşzamanlılık bütçeyi aştı")

    def test_idle_callers_are_pruned_so_the_table_cannot_grow_forever(self) -> None:
        limiter = RateLimiter(limit=1, window_seconds=1)
        for index in range(1500):
            limiter.allow(f"caller-{index}", now=index)
        self.assertLess(len(limiter._hits), 1500)


class EnvironmentTests(unittest.TestCase):
    def test_defaults_are_applied_when_nothing_is_set(self) -> None:
        limiter = from_environment({})
        self.assertEqual(limiter.limit, 60)
        self.assertEqual(limiter.window_seconds, 60)

    def test_values_are_read_from_the_environment(self) -> None:
        limiter = from_environment({"SAHNE_RATE_LIMIT": "5", "SAHNE_RATE_WINDOW": "30"})
        self.assertEqual(limiter.limit, 5)
        self.assertEqual(limiter.window_seconds, 30)

    def test_unparsable_values_fall_back_to_the_defaults(self) -> None:
        limiter = from_environment({"SAHNE_RATE_LIMIT": "abc", "SAHNE_RATE_WINDOW": ""})
        self.assertEqual(limiter.limit, 60)
        self.assertEqual(limiter.window_seconds, 60)


if __name__ == "__main__":
    unittest.main()
