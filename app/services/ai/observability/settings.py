"""进程内同步配置快照与后台刷新。

导出钩子（``should_export_span`` / ``mask_otel_spans``）运行在 OTel 的后台导出线程里，
**不能 await**，所以这里对外只提供同步的 ``get_snapshot()``：后台协程每 30s 从数据库刷一次，
写入一个由 ``threading.Lock`` 保护的引用。

刷新失败不清空快照 —— 网络抖动或主库不可用时应继续沿用上一次的有效配置，而不是把正在
工作的追踪整体关掉。
"""

import asyncio
import hashlib
import logging
import threading
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional

from app.services.ai.observability import config_store, credentials

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 30

STATUS_DISABLED = "disabled"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_ENABLED = "enabled"
STATUS_DEGRADED = "degraded"


@dataclass(frozen=True)
class LangfuseSnapshot:
    """某一时刻的有效配置。不可变，便于跨线程安全读取。"""

    enabled: bool = False
    host: Optional[str] = None
    public_key: Optional[str] = None
    secret_key: Optional[str] = None
    sample_rate: float = 1.0
    capture_content: bool = True
    environment: Optional[str] = None
    release: Optional[str] = None
    timeout_seconds: int = 5
    trace_url_template: Optional[str] = None
    status: str = STATUS_DISABLED
    client_fingerprint: Optional[str] = None
    last_error: Optional[str] = None


_DEFAULT_SNAPSHOT = LangfuseSnapshot()

_snapshot_lock = threading.Lock()
_snapshot: LangfuseSnapshot = _DEFAULT_SNAPSHOT
_refresh_task: Optional[asyncio.Task] = None
_refresh_listeners: list = []


def _compute_fingerprint(
    host: Optional[str],
    public_key: Optional[str],
    secret_key: Optional[str],
    timeout_seconds: int,
) -> Optional[str]:
    """连接参数指纹：变化时才需要重建 Langfuse client。"""
    if not (host and public_key and secret_key):
        return None
    material = f"{host}|{public_key}|{secret_key}|{timeout_seconds}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _build_snapshot(config: Dict[str, Any]) -> LangfuseSnapshot:
    """把配置字典判定成快照（含状态与错误原因）。"""
    host = config.get("host")
    public_key = config.get("public_key")
    secret_key = config.get("secret_key")
    timeout_seconds = int(config.get("timeout_seconds") or 5)

    snapshot = LangfuseSnapshot(
        enabled=bool(config.get("enabled")),
        host=host,
        public_key=public_key,
        secret_key=secret_key,
        sample_rate=float(config.get("sample_rate") or 0.0),
        capture_content=bool(config.get("capture_content")),
        environment=config.get("environment"),
        release=config.get("release"),
        timeout_seconds=timeout_seconds,
        trace_url_template=config.get("trace_url_template"),
    )

    if not snapshot.enabled:
        return replace(snapshot, status=STATUS_DISABLED)

    missing = [
        name
        for name, value in (("host", host), ("public_key", public_key), ("secret_key", secret_key))
        if not value
    ]
    if missing:
        return replace(
            snapshot,
            status=STATUS_NOT_CONFIGURED,
            last_error=f"配置不完整，缺少: {', '.join(missing)}",
        )

    fingerprint = _compute_fingerprint(host, public_key, secret_key, timeout_seconds)
    return replace(snapshot, status=STATUS_ENABLED, client_fingerprint=fingerprint)


def get_snapshot() -> LangfuseSnapshot:
    """读取当前快照（同步、线程安全，可被 OTel 导出线程调用）。"""
    with _snapshot_lock:
        return _snapshot


def is_tracing_ready() -> bool:
    """是否具备创建 span 的条件（开关打开且连接参数完整）。"""
    return get_snapshot().status == STATUS_ENABLED


def add_refresh_listener(callback) -> None:
    """注册快照刷新回调（由 manager 用来判断是否需要重建 client）。

    回调必须是同步函数：它可能被刷新协程调用，也可能被测试直接触发。
    """
    if callback not in _refresh_listeners:
        _refresh_listeners.append(callback)


def clear_refresh_listeners() -> None:
    """清空刷新回调（仅测试使用，避免用例之间互相污染）。"""
    _refresh_listeners.clear()


def _notify_listeners(snapshot: LangfuseSnapshot) -> None:
    for callback in list(_refresh_listeners):
        try:
            callback(snapshot)
        except Exception as exc:  # 监听器异常不能影响配置刷新本身
            logger.warning("Langfuse 快照刷新监听器执行失败: %s", exc)


async def refresh_snapshot() -> LangfuseSnapshot:
    """从数据库刷新快照。

    - 读库/解密失败：保留上一次快照，只追加 ``last_error``；
    - 密钥密文损坏：置 ``degraded``（这是配置错误，必须显式暴露而不是沿用旧值）。
    """
    global _snapshot

    try:
        config = await config_store.load_config()
    except credentials.LangfuseCredentialError as exc:
        logger.warning("Langfuse 密钥解密失败，追踪进入 degraded: %s", exc)
        with _snapshot_lock:
            _snapshot = replace(_snapshot, status=STATUS_DEGRADED, last_error=str(exc))
            return _snapshot
    except Exception as exc:  # 主库不可用等
        logger.warning("Langfuse 配置读取失败，沿用上一次快照: %s", exc)
        with _snapshot_lock:
            _snapshot = replace(_snapshot, last_error=f"{type(exc).__name__}: {exc}")
            return _snapshot

    new_snapshot = _build_snapshot(config)
    with _snapshot_lock:
        previous = _snapshot
        _snapshot = new_snapshot

    if previous.client_fingerprint != new_snapshot.client_fingerprint:
        logger.info(
            "Langfuse 配置快照已更新: status=%s fingerprint=%s",
            new_snapshot.status,
            new_snapshot.client_fingerprint,
        )

    _notify_listeners(new_snapshot)
    return new_snapshot


async def _refresh_loop(interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await refresh_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - refresh_snapshot 已自兜底
            logger.warning("Langfuse 配置刷新循环异常: %s", exc)


def start_refresh_task(interval_seconds: int = REFRESH_INTERVAL_SECONDS) -> asyncio.Task:
    """启动刷新协程（幂等）。

    即使当前是关闭状态也必须启动：否则从系统设置页打开的开关永远不会生效。
    """
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return _refresh_task
    _refresh_task = asyncio.create_task(_refresh_loop(interval_seconds))
    return _refresh_task


async def stop_refresh_task() -> None:
    """停止刷新协程（用于优雅关闭与测试）。"""
    global _refresh_task
    task = _refresh_task
    _refresh_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: B014 - 关闭阶段不抛
        pass


def reset_for_tests() -> None:
    """仅测试使用：恢复默认快照并丢弃刷新任务引用。"""
    global _snapshot, _refresh_task
    with _snapshot_lock:
        _snapshot = _DEFAULT_SNAPSHOT
    if _refresh_task is not None and not _refresh_task.done():
        _refresh_task.cancel()
    _refresh_task = None
