import os
import resource
import time
from collections import defaultdict, deque
from threading import Lock


class MetricsRegistry:
    def __init__(self, retention_seconds=86400):
        self.retention_seconds = retention_seconds
        self.started_at = time.monotonic()
        self._events = deque()
        self._gauges = {}
        self._totals = defaultdict(float)
        self._lock = Lock()
        self._last_process_time = time.process_time()
        self._last_wall_time = time.monotonic()

    def _prune(self, now):
        cutoff = now - self.retention_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def increment(self, name, value=1, guild_id=None):
        now = time.time()
        key = str(name)
        with self._lock:
            self._totals[key] += value
            self._events.append((now, key, float(value), guild_id))
            self._prune(now)

    def set_gauge(self, name, value):
        with self._lock:
            self._gauges[str(name)] = float(value)

    def gauge(self, name, default=0.0):
        with self._lock:
            return self._gauges.get(str(name), default)

    def total(self, name):
        with self._lock:
            return self._totals.get(str(name), 0.0)

    def count(self, name, seconds=None, guild_id=None):
        now = time.time()
        key = str(name)
        with self._lock:
            self._prune(now)
            cutoff = now - seconds if seconds is not None else None
            return sum(
                value
                for timestamp, event_name, value, event_guild_id in self._events
                if event_name == key
                and (cutoff is None or timestamp >= cutoff)
                and (guild_id is None or event_guild_id == guild_id)
            )

    def snapshot(self, seconds=3600, guild_id=None):
        now = time.time()
        with self._lock:
            self._prune(now)
            cutoff = now - seconds
            values = defaultdict(float)
            for timestamp, name, value, event_guild_id in self._events:
                if timestamp < cutoff:
                    continue
                if guild_id is not None and event_guild_id != guild_id:
                    continue
                values[name] += value
            gauges = dict(self._gauges)
            totals = dict(self._totals)
        return {
            "window_seconds": seconds,
            "events": dict(values),
            "gauges": gauges,
            "totals": totals,
            "uptime_seconds": max(0.0, time.monotonic() - self.started_at),
        }

    def process_stats(self):
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        elapsed_wall = max(0.001, now_wall - self._last_wall_time)
        elapsed_cpu = max(0.0, now_cpu - self._last_process_time)
        self._last_wall_time = now_wall
        self._last_process_time = now_cpu
        cpu_percent = min(100.0, max(0.0, elapsed_cpu / elapsed_wall * 100.0))
        memory_mb = self._current_memory_mb()
        try:
            load_1m, load_5m, load_15m = os.getloadavg()
        except (AttributeError, OSError):
            load_1m = load_5m = load_15m = 0.0
        self.set_gauge("process.cpu_percent", cpu_percent)
        self.set_gauge("process.memory_mb", memory_mb)
        self.set_gauge("system.load_1m", load_1m)
        self.set_gauge("system.load_5m", load_5m)
        self.set_gauge("system.load_15m", load_15m)
        return {
            "cpu_percent": cpu_percent,
            "memory_mb": memory_mb,
            "load_1m": load_1m,
            "load_5m": load_5m,
            "load_15m": load_15m,
        }

    @staticmethod
    def _current_memory_mb():
        try:
            with open("/proc/self/statm", "r", encoding="utf-8") as handle:
                resident_pages = int(handle.read().split()[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            return resident_pages * page_size / 1024 / 1024
        except (OSError, ValueError, IndexError):
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if os.uname().sysname == "Darwin":
                return usage / 1024 / 1024
            return usage / 1024


metrics = MetricsRegistry()


def record_event(name, value=1, guild_id=None):
    metrics.increment(name, value, guild_id)


def set_gauge(name, value):
    metrics.set_gauge(name, value)


def metric_count(name, seconds=None, guild_id=None):
    return metrics.count(name, seconds, guild_id)


def metric_snapshot(seconds=3600, guild_id=None):
    return metrics.snapshot(seconds, guild_id)


def process_stats():
    return metrics.process_stats()
