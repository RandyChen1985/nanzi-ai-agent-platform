"""进程内 HTTP 请求指标：QPS / P95 / 5xx 率 / 并发数。

只为「系统诊断 → 实例」页服务，不接 Prometheus。所有统计都滚动保留 60 秒，
因此得到的是**本节点视角**的指标；跨节点对比由前端把各实例的值并排展示。

为什么不用 ``BaseHTTPMiddleware``：它对流式响应（聊天 SSE）会把响应体缓冲下来，
会改变首个 token 的到达时间。这里用纯 ASGI 中间件，只观察消息、不碰 body。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple

# 统计窗口：QPS 与 P95 的口径都是「最近 60 秒」
WINDOW_SECONDS = 60
# 单进程最多保留的请求样本数，防止高 QPS 时内存无界增长（超出后丢最旧的）
MAX_SAMPLES = 20000

# 对话流的真实入口。**这不是 `/api/portal/chat`**：门户挂件与网页聊天全部走
# `/api/v1/chat/conversation/{id}/stream-events`（SSE），而 `/api/portal/chat`
# 下只有一个 `/intent`。前缀写错时「对话流进行中」永远是 0——看上去像「没人
# 聊天」，其实是根本没统计到。
CHAT_PATH_PREFIXES = ("/api/v1/chat", "/api/portal/chat")
# 只把流式请求算作「对话流」：同前缀下的历史、反馈、菜单这类短请求不该把它带偏。
CHAT_STREAM_MARKER = "/stream"

CHAT_BUCKET = "chat"
API_BUCKET = "api"


def classify_path(path: str) -> str:
    """对话流 = 对话前缀下的**流式**请求（SSE 长连接）。

    中间件要等整个流结束才 `end()`，所以这类请求的 inflight 就是「此刻正在
    流式生成的对话数」，名实相符。
    """
    if CHAT_STREAM_MARKER in path and path.startswith(CHAT_PATH_PREFIXES):
        return CHAT_BUCKET
    return API_BUCKET


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


class LoopLagMonitor:
    """事件循环延迟：后台协程每秒量一次，取最近 60 秒的 P95。

    为什么单列这个数：QPS / P95 都是 **HTTP 视角**。事件循环被同步代码堵住
    （大 json 序列化、同步加密、没走线程的 CPU 活）时，表现是「请求不多但都
    很慢」，从流量指标看不出是谁干的。把这个数并排放在旁边，就能分清
    「外面没人来」和「里面堵住了」。

    采样方式是拿 `loop.time()` 自证：定时器本应在 1.00s 后醒，实际晚了多少
    就是延迟多少。不依赖任何第三方库，也不会给自己制造负载。
    """

    #: 采样间隔（秒）。测试会改小，生产保持 1s
    INTERVAL_SECONDS = 1.0
    #: 统计窗口与样本上限：1s 一个点，60 个点刚好覆盖一分钟
    WINDOW_SECONDS = 60
    MAX_SAMPLES = 600

    def __init__(self, interval_seconds: float | None = None) -> None:
        self._interval = interval_seconds or self.INTERVAL_SECONDS
        self._lock = threading.Lock()
        #: (时间戳, 延迟毫秒)
        self._samples: Deque[Tuple[float, float]] = deque()
        self._task = None

    # ---- 生命周期 -----------------------------------------------------
    def start(self) -> None:
        """在事件循环里起后台采样。重复调用无副作用。"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="loop-lag-monitor")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - 正常取消路径
            pass

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time() + self._interval
        while True:
            await asyncio.sleep(max(0.0, expected - loop.time()))
            now = loop.time()
            self.observe(max(0.0, (now - expected) * 1000.0), timestamp=time.time())
            expected = now + self._interval

    # ---- 采样与快照 ---------------------------------------------------
    def observe(self, lag_ms: float, timestamp: float | None = None) -> None:
        """记一个延迟样本。用 `loop.time()` 算出来的值，不接受外部时钟。"""
        with self._lock:
            self._samples.append((timestamp if timestamp is not None else time.time(), float(lag_ms)))
            while len(self._samples) > self.MAX_SAMPLES:
                self._samples.popleft()

    def snapshot(self) -> Dict[str, float]:
        now = time.time()
        cutoff = now - self.WINDOW_SECONDS
        with self._lock:
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            lags = sorted(item[1] for item in self._samples)

        if not lags:
            return {"loop_lag_p95_ms": 0.0, "loop_lag_max_ms": 0.0, "loop_lag_samples": 0}
        index = min(len(lags) - 1, int(len(lags) * 0.95))
        return {
            "loop_lag_p95_ms": round(lags[index], 1),
            "loop_lag_max_ms": round(lags[-1], 1),
            "loop_lag_samples": len(lags),
        }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


loop_lag = LoopLagMonitor()


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
