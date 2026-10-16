"""Daily writing statistics shared across all projects."""

from __future__ import annotations

import threading
from datetime import date, timedelta
from pathlib import Path

from .fileio import atomic_write_json, read_json


class Stats:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        data = read_json(path, {}) or {}
        self.days: dict[str, dict] = data.get("days", {}) if isinstance(data, dict) else {}

    def record(self, delta: int, today: date | None = None) -> None:
        if not delta:
            return
        key = (today or date.today()).isoformat()
        with self._lock:
            d = self.days.setdefault(key, {"added": 0, "deleted": 0})
            if delta > 0:
                d["added"] += delta
            else:
                d["deleted"] += -delta
            atomic_write_json(self.path, {"days": self.days})

    def today(self, today: date | None = None) -> dict:
        return self.days.get((today or date.today()).isoformat(), {"added": 0, "deleted": 0})

    def streak(self, goal: int, today: date | None = None) -> int:
        """Consecutive days (ending today or yesterday) with at least ``max(goal, 1)`` words added."""
        t = today or date.today()
        need = max(goal, 1)
        day = t if self.days.get(t.isoformat(), {}).get("added", 0) >= need else t - timedelta(days=1)
        n = 0
        while self.days.get(day.isoformat(), {}).get("added", 0) >= need:
            n += 1
            day -= timedelta(days=1)
        return n

    def summary(self, goal: int, days: int = 371, today: date | None = None) -> dict:
        t = today or date.today()
        series = []
        for i in range(days - 1, -1, -1):
            d = t - timedelta(days=i)
            series.append({"date": d.isoformat(), "added": self.days.get(d.isoformat(), {}).get("added", 0)})
        week = sum(x["added"] for x in series[-7:])
        return {"today": self.today(t), "goal": goal, "streak": self.streak(goal, t), "week": week, "series": series, "best": max((v.get("added", 0) for v in self.days.values()), default=0)}
