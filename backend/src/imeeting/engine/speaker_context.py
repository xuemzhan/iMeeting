"""Short-range speaker continuity for segments without usable embeddings."""

from __future__ import annotations

from collections import deque

from .speaker import SpeakerResolution


class SpeakerContext:
    """Track recent resolved speakers and reuse only strongly consistent ones."""

    def __init__(self, maxlen: int = 8) -> None:
        self._history: deque[tuple[str, str, int]] = deque(maxlen=maxlen)

    def record(self, label: str, key: str, end_ms: int) -> None:
        if label and key:
            self._history.append((label, key, end_ms))

    def resolve(
        self,
        start_ms: int,
        *,
        max_gap_ms: int,
        source: str,
    ) -> SpeakerResolution | None:
        if not self._history:
            return None
        recent = [
            item
            for item in list(self._history)[-5:]
            if start_ms - item[2] <= max_gap_ms
        ]
        if not recent:
            return None
        counts: dict[tuple[str, str], int] = {}
        for label, key, _end in recent:
            if label and key:
                counts[(label, key)] = counts.get((label, key), 0) + 1
        if not counts:
            return None
        (label, key), count = max(counts.items(), key=lambda item: item[1])
        if count / len(recent) < 0.75:
            return None
        return SpeakerResolution(label, key, 0.0, source)

    def snapshot(self) -> list[tuple[str, str, int]]:
        return list(self._history)

