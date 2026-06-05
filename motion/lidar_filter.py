"""TF-Luna single-point LiDAR filtering for the STATE range_mm field."""

from __future__ import annotations

from collections import deque


# TF-Luna spec: ~0.2–8 m; desk use is usually 150–2500 mm.
DEFAULT_MIN_MM = 120
DEFAULT_MAX_MM = 8000
DEFAULT_WINDOW = 7


class LidarFilter:
    """Median-smooth and reject invalid TF-Luna readings (0, timeout, spikes)."""

    def __init__(
        self,
        *,
        min_mm: int = DEFAULT_MIN_MM,
        max_mm: int = DEFAULT_MAX_MM,
        window: int = DEFAULT_WINDOW,
    ) -> None:
        self.min_mm = min_mm
        self.max_mm = max_mm
        self._window = max(3, window)
        self._samples: deque[int] = deque(maxlen=self._window)
        self._last_good: int = -1
        self._stale_count = 0

    def is_valid_raw(self, raw_mm: int) -> bool:
        return self.min_mm <= raw_mm <= self.max_mm

    def ingest(self, raw_mm: int) -> int:
        """Return smoothed range in mm, or -1 if no trustworthy sample."""
        if not self.is_valid_raw(raw_mm):
            self._stale_count += 1
            if self._stale_count >= 3:
                self._samples.clear()
            return self._last_good if self._stale_count < 5 else -1

        self._stale_count = 0
        self._samples.append(int(raw_mm))
        ordered = sorted(self._samples)
        mid = ordered[len(ordered) // 2]
        self._last_good = mid
        return mid

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def last_good(self) -> int:
        return self._last_good

    def status(self) -> dict:
        return {
            "last_good_mm": self._last_good,
            "samples": len(self._samples),
            "stale_streak": self._stale_count,
            "valid": self._last_good > 0,
        }
