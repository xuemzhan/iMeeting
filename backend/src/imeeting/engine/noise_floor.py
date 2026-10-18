"""Adaptive ambient-noise tracking for energy-based segmentation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class NoiseUpdate:
    noise_floor: float
    start_threshold: float
    continue_threshold: float
    snr_db: float
    metric_due: bool
    warning_due: bool


class NoiseFloorTracker:
    """Track a rolling noise floor and derive speech thresholds."""

    def __init__(
        self,
        base_threshold: float,
        *,
        max_threshold: float = 0.06,
        history_size: int = 64,
    ) -> None:
        self.max_threshold = max_threshold
        self.noise_floor = 0.0015
        self.start_threshold = base_threshold
        self.continue_threshold = base_threshold * 0.6
        self.last_snr_db = 0.0
        self._energy_hist: deque[float] = deque(maxlen=history_size)
        self._low_snr_seconds = 0.0
        self._warned = False
        self._metric_tick = 0

    def update(self, energy: float, seconds: float) -> NoiseUpdate | None:
        if energy <= 0:
            return None
        self._energy_hist.append(energy)
        if len(self._energy_hist) < 8:
            self.noise_floor = min(self.noise_floor, energy)
            return self._snapshot()

        measured = float(np.percentile(self._energy_hist, 10))
        target = min(measured, self.max_threshold / 3)
        self.noise_floor = 0.92 * self.noise_floor + 0.08 * target
        self.noise_floor = max(
            min(self.noise_floor, self.max_threshold / 2),
            1e-5,
        )
        self.start_threshold = min(
            max(self.noise_floor * 3.5, 0.005),
            self.max_threshold,
        )
        self.continue_threshold = min(
            max(self.noise_floor * 2.0, 0.003),
            self.max_threshold,
        )

        speech = float(np.percentile(self._energy_hist, 90))
        dynamic = speech / (self.noise_floor + 1e-6)
        if dynamic >= 2.0:
            self.start_threshold = min(
                self.start_threshold,
                max(speech * 0.45, self.noise_floor * 1.6),
            )
            self.continue_threshold = min(
                self.continue_threshold,
                max(speech * 0.30, self.noise_floor * 1.2),
            )
        self.last_snr_db = float(
            20 * np.log10((speech + 1e-9) / (self.noise_floor + 1e-9))
        )
        self._metric_tick += 1
        metric_due = self._metric_tick % 5 == 0

        if self.last_snr_db < 12.0:
            self._low_snr_seconds += seconds
        elif self.last_snr_db > 15.0:
            self._low_snr_seconds = 0.0
        warning_due = self._low_snr_seconds >= 5.0 and not self._warned
        if warning_due:
            self._warned = True

        update = self._snapshot()
        update.metric_due = metric_due
        update.warning_due = warning_due
        return update

    def _snapshot(self) -> NoiseUpdate:
        return NoiseUpdate(
            noise_floor=self.noise_floor,
            start_threshold=self.start_threshold,
            continue_threshold=self.continue_threshold,
            snr_db=self.last_snr_db,
            metric_due=False,
            warning_due=False,
        )

