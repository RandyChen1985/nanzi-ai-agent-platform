"""进程内 HTTP 请求指标：QPS / P95 / 5xx 率 / 并发数。

只为「系统诊断 → 实例」页服务，不接 Prometheus。所有统计都滚动保留 60 秒，
因此得到的是**本节点视角**的指标；跨节点对比由前端把各实例的值并排展示。

为什么不用 ``BaseHTTPMiddleware``：它对流式响应（聊天 SSE）会把响应体缓冲下来，
会改变首个 token 的到达时间。这里用纯 ASGI 中间件，只观察消息、不碰 body。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple

# 统计窗口：QPS 与 P95 的口径都是「最近 60 秒」
WINDOW_SECONDS = 60
# 单进程最多保留的请求样本数，防止高 QPS 时内存无界增长（超出后丢最旧的）
MAX_SAMPLES = 20000

# 并发分类：只关心对话流，其余统一算普通 API
CHAT_PATH_PREFIX = "/api/portal/chat"
CHAT_BUCKET = "chat"
API_BUCKET = "api"


def classify_path(path: str) -> str:
    return CHAT_BUCKET if path.startswith(CHAT_PATH_PREFIX) else API_BUCKET


class RequestMetrics:
    """线程安全（中间件跑在事件循环里，但快照可能被心跳协程并发读取）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (时间戳, 耗时毫秒, 状态码)
        self._samples: Deque[Tuple[float, float, int]] = deque()
        self._inflight_total = 0
        self._inflight_by_bucket: Dict[str, int] = {CHAT_BUCKET: 0, API_BUCKET: 0}
        self._total_served = 0

    def begin(self, bucket: str) -> None:
        with self._lock:
            self._inflight_total += 1
            self._inflight_by_bucket[bucket] = self._inflight_by_bucket.get(bucket, 0) + 1

    def end(self, bucket: str, duration_ms: float, status_code: int) -> None:
        now = time.time()
        with self._lock:
            self._inflight_total = max(0, self._inflight_total - 1)
            current = self._inflight_by_bucket.get(bucket, 0)
            self._inflight_by_bucket[bucket] = max(0, current - 1)
            self._total_served += 1
            self._samples.append((now, float(duration_ms), int(status_code)))
            if len(self._samples) > MAX_SAMPLES:
                for _ in range(len(self._samples) - MAX_SAMPLES):
                    self._samples.popleft()

    def _trim_locked(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def snapshot(self) -> Dict[str, object]:
        now = time.time()
        with self._lock:
            self._trim_locked(now)
            samples = list(self._samples)
            inflight = self._inflight_total
            inflight_by_bucket = dict(self._inflight_by_bucket)
            total_served = self._total_served

        count = len(samples)
        durations = sorted(item[1] for item in samples)
        errors = sum(1 for item in samples if item[2] >= 500)

        def percentile(ratio: float) -> float:
            if not durations:
                return 0.0
            index = min(len(durations) - 1, int(len(durations) * ratio))
            return round(durations[index], 1)

        return {
            "requests_1m": count,
            "qps": round(count / WINDOW_SECONDS, 2),
            "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99),
            "error_rate": round(errors / count * 100, 2) if count else 0.0,
            "inflight": inflight,
            "chat_inflight": inflight_by_bucket.get(CHAT_BUCKET, 0),
            "served_total": total_served,
            "window_seconds": WINDOW_SECONDS,
        }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._total_served = 0


metrics = RequestMetrics()


class RequestMetricsMiddleware:
    """纯 ASGI 中间件：只统计 /api/ 请求，不读取也不改写响应体。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "") or ""
        if not path.startswith("/api/"):
            return await self.app(scope, receive, send)

        bucket = classify_path(path)
        started = time.perf_counter()
        metrics.begin(bucket)
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status_holder["status"] = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            metrics.end(bucket, (time.perf_counter() - started) * 1000.0, status_holder["status"])
