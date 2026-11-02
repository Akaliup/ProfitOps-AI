"""Process-local metrics and request rate limiting.

The deployment target is a single API process (plus the in-process analysis
worker), so a Prometheus client dependency and a shared Redis are unnecessary.
The registry is thread-safe, exposes the Prometheus text format, and resets
cleanly in tests. Rate-limit buckets are process-local: a multi-replica
deployment must move them to a shared store.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from dataclasses import dataclass, field

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

RATE_LIMIT_EXEMPT_PATHS = frozenset({"/health", "/metrics", "/openapi.json", "/docs", "/redoc"})

# Fixed labels keep HTTP metric cardinality bounded. A request that matches no
# route (or is rejected before routing by the rate limiter) must never use its
# raw URL as a label: an arbitrary client can mint unlimited distinct paths.
UNMATCHED_ROUTE_LABEL = "<unmatched>"
RATE_LIMITED_ROUTE_LABEL = "<rate_limited>"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def normalize_path(path: str) -> str:
    """Collapse resource IDs so metric label cardinality stays bounded."""
    parts = [
        ":id" if _UUID_RE.match(segment) or segment.isdigit() else segment
        for segment in path.split("/")
    ]
    return "/".join(parts)[:120]


def route_label(scope, default: str = UNMATCHED_ROUTE_LABEL) -> str:
    """Prefer the matched route template over the concrete request path.

    Starlette sets ``scope["route"]`` only after routing. Requests that never
    match (404s, or the rate limiter's early return) get a fixed label.
    """
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template or default


def _metric_name(name: str) -> str:
    return name if name.startswith("profitops_") else f"profitops_{name}"


def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels: tuple[tuple[str, str], ...], extra: dict[str, str] | None = None) -> str:
    values = dict(labels)
    if extra:
        values.update(extra)
    if not values:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(values.items())) + "}"


def _format_number(value: float) -> str:
    # Gauges are stored as ints in places, and int has no is_integer() on 3.11.
    if isinstance(value, int) or value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * len(BUCKETS))

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        for index, bound in enumerate(BUCKETS):
            if value <= bound:
                self.buckets[index] += 1
                break


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (_metric_name(name), _labels_key(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = (_metric_name(name), _labels_key(labels))
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = (_metric_name(name), _labels_key(labels))
        with self._lock:
            histogram = self._histograms.get(key)
            if histogram is None:
                histogram = _Histogram()
                self._histograms[key] = histogram
            histogram.observe(max(0.0, value))

    def counter_value(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._counters.get((_metric_name(name), _labels_key(labels)), 0.0)

    def gauge_value(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._gauges.get((_metric_name(name), _labels_key(labels)), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {key: _Histogram(value.count, value.total, list(value.buckets)) for key, value in self._histograms.items()}
        lines: list[str] = []
        rendered: set[str] = set()

        def header(name: str, kind: str) -> None:
            if name in rendered:
                return
            rendered.add(name)
            short = name[len("profitops_"):] if name.startswith("profitops_") else name
            lines.append(f"# HELP {name} ProfitOps {short.replace('_', ' ')}.")
            lines.append(f"# TYPE {name} {kind}")

        for (name, labels), value in sorted(counters.items()):
            header(name, "counter")
            lines.append(f"{name}{_format_labels(labels)} {_format_number(value)}")
        for (name, labels), value in sorted(gauges.items()):
            header(name, "gauge")
            lines.append(f"{name}{_format_labels(labels)} {_format_number(value)}")
        for (name, labels), histogram in sorted(histograms.items()):
            header(name, "histogram")
            cumulative = 0
            for bound, count in zip(BUCKETS, histogram.buckets):
                cumulative += count
                lines.append(
                    f"{name}_bucket{_format_labels(labels, {'le': _format_number(bound)})} {cumulative}"
                )
            lines.append(f"{name}_bucket{_format_labels(labels, {'le': '+Inf'})} {histogram.count}")
            lines.append(f"{name}_sum{_format_labels(labels)} {_format_number(histogram.total)}")
            lines.append(f"{name}_count{_format_labels(labels)} {histogram.count}")
        return "\n".join(lines) + ("\n" if lines else "")


METRICS = MetricsRegistry()


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    """Thread-safe token buckets keyed by caller and scope.

    ``capacity`` is the burst size and ``window`` is the period in which the
    bucket refills completely. ``allow`` returns ``(allowed, retry_after)``.
    """

    def __init__(self, max_buckets: int = 8192) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._max_buckets = max(1, max_buckets)

    def allow(
        self,
        key: str,
        *,
        capacity: int,
        window: float,
        now: float | None = None,
    ) -> tuple[bool, float]:
        if capacity <= 0 or window <= 0:
            return True, 0.0
        now = time.monotonic() if now is None else now
        rate = capacity / window
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._make_room(now)
                bucket = _Bucket(float(capacity), now)
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated)
                bucket.tokens = min(float(capacity), bucket.tokens + elapsed * rate)
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            return False, (1.0 - bucket.tokens) / rate

    def _make_room(self, now: float) -> None:
        """Keep ``_max_buckets`` a hard bound, even during a key burst.

        Stale buckets are discarded first. If the burst is still filling the
        table, the least-recently-used bucket is evicted before the new key is
        inserted. Without this second step a burst of unique keys inside the
        one-hour stale window would grow the dictionary without limit.
        """
        if len(self._buckets) < self._max_buckets:
            return
        stale = [key for key, bucket in self._buckets.items() if now - bucket.updated > 3600]
        for key in stale:
            self._buckets.pop(key, None)
        while len(self._buckets) >= self._max_buckets:
            oldest = min(self._buckets, key=lambda key: self._buckets[key].updated)
            self._buckets.pop(oldest, None)


def rate_limit_scope(path: str, method: str) -> str:
    if path == "/api/auth/login":
        return "login"
    if path == "/api/analyses" and method.upper() == "POST":
        return "analysis"
    return "default"


def rate_limit_key(request, token: str | None) -> str:
    host = request.client.host if request.client else "unknown"
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"{host}:{digest}"
    return f"{host}:anonymous"


def retry_after_header(seconds: float) -> str:
    return str(max(1, math.ceil(seconds)))
