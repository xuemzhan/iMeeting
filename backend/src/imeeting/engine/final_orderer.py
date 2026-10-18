"""Order finalized transcript segments without blocking indefinitely."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PendingFinal:
    payload: dict | None
    ready_at: float = 0.0


class FinalOrderer:
    """Track processed sequence numbers and release finals in timeline order."""

    def __init__(self, reorder_timeout: float) -> None:
        self.reorder_timeout = reorder_timeout
        self._pending: dict[int, PendingFinal] = {}
        self._next_seq = 0
        self._emitted: set[int] = set()

    def submit(self, seq: int, payload: dict, ready_at: float) -> None:
        self._pending[seq] = PendingFinal(payload=payload, ready_at=ready_at)

    def resolve(self, seq: int) -> None:
        """Mark a sequence as processed even when it produced no payload."""
        self._pending.setdefault(seq, PendingFinal(payload=None))

    def take_next(self, now: float) -> tuple[int, PendingFinal, bool] | None:
        """Return the next payload, allowing a bounded wait for slow segments."""
        if self._next_seq in self._pending:
            return self._take(self._next_seq, out_of_order=False)

        if self.reorder_timeout > 0 and self._pending:
            oldest = min(self._pending)
            item = self._pending[oldest]
            if item.ready_at and now - item.ready_at >= self.reorder_timeout:
                return self._take(oldest, out_of_order=True)
        return None

    def _take(self, seq: int, *, out_of_order: bool) -> tuple[int, PendingFinal, bool]:
        item = self._pending.pop(seq)
        self._emitted.add(seq)
        while self._next_seq in self._emitted:
            self._next_seq += 1
        return seq, item, out_of_order

