"""Shared spend ledger + request rate limiter used by every worker.

Before dispatch a worker reserves the worst-case cost of its call (estimated input + max_tokens output).
Admission stops when spent + reserved + next reservation would exceed the cap. Actual cost replaces the
reservation when the call returns; unknown outcomes (timeouts) keep the reservation as a charged estimate.
"""
import threading
import time

from . import config


class BudgetExceeded(Exception):
    pass


class Ledger:
    def __init__(self, cap_usd, already_spent=0.0, max_rpm=1000):
        self.cap = cap_usd
        self.spent = already_spent
        self.reserved = 0.0
        self.lock = threading.Lock()
        self.min_interval = 60.0 / max_rpm
        self.next_slot = 0.0

    @staticmethod
    def worst_case(model, est_input_tokens, max_tokens):
        p = config.PRICES[model]
        # cache_write is the most expensive input category; assume all input could be billed there.
        return (est_input_tokens * max(p["input"], p["cache_write"]) + max_tokens * p["output"]) / 1e6

    def reserve(self, amount):
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                raise BudgetExceeded(f"spent {self.spent:.4f} + reserved {self.reserved:.4f} + next {amount:.4f} > cap {self.cap:.2f}")
            self.reserved += amount
            # simple global pacing (token bucket of size 1)
            now = time.monotonic()
            wait = max(0.0, self.next_slot - now)
            self.next_slot = max(now, self.next_slot) + self.min_interval
        if wait:
            time.sleep(wait)

    def settle(self, reserved, actual):
        with self.lock:
            self.reserved -= reserved
            self.spent += actual

    def snapshot(self):
        with self.lock:
            return {"cap_usd": self.cap, "spent_usd": round(self.spent, 6), "reserved_usd": round(self.reserved, 6)}
