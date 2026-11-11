"""多实例心跳与运行态登记（基于共享 Redis）。

每个实例每 15 秒把自己的快照写进一个带 TTL 的 HASH；诊断页只读这一处聚合结果，
因此「多个实例」在页面上就是并列的几个心跳，不需要额外的注册中心或数据库表。

实例身份（下文所有 ``{host}``）按 ``NODE_ID`` > ``INSTANCE_NAME`` > hostname 解析：
容器里 hostname 通常是随机串（``80bb99ffd85a``），多实例部署时靠 ``NODE_ID``
给每个进程一个稳定可读的名字；真实主机名另存为快照字段 ``hostname``，排查时对照用。

Key 设计（所有实例连的是同一个 Redis）：

``nanzi:instance:{host}``          HASH，TTL 45s（心跳 15s × 3，过期即判离线）
``nanzi:instances:index``          SET<host>，启动时 SADD，读时按 TTL 剔除已退出节点
``nanzi:instance:{host}:series``   ZSET，每分钟 1 点，保留 24 小时后自动裁剪
``nanzi:instance:alias:{host}``    STRING，页面编辑的别名，不设 TTL

「主 / 从」直接取 ``settings.TASK_SCHEDULER_ENABLED``：它决定这个进程要不要跑
定时任务调度，页面只做展示与多主校验，不提供在线切换——那会造出「页面说是从、
进程仍在跑调度」的假一致。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from sqlalchemy import text

from app.core.config import settings
from app.core.redis import get_redis
from app.core.request_metrics import loop_lag, metrics

logger = logging.getLogger(__name__)

INSTANCE_INDEX_KEY = "nanzi:instances:index"
INSTANCE_KEY_PREFIX = "nanzi:instance:"
INSTANCE_ALIAS_PREFIX = "nanzi:instance:alias:"
ONLINE_PRESENCE_ZSET_KEY = "presence:users"

# 心跳 15 秒写一次，TTL 留 3 个周期：一次 GC 停顿或短暂网络抖动不会误判离线
INSTANCE_TTL_SECONDS = 45
HEARTBEAT_INTERVAL_SECONDS = 15
# 趋势点每分钟一个，保留一天
SERIES_SAMPLE_INTERVAL_SECONDS = 60
SERIES_TTL_SECONDS = 24 * 3600
SERIES_MAX_POINTS = 1440
# 卡片上的迷你趋势只要最近 1 小时
SPARK_POINTS = 60
# 详情接口返回给前端的最大点数（超出按步长抽样，避免一次吐 1440 个点）
MAX_RETURN_POINTS = 240

ALIAS_MAX_LENGTH = 32

# 进程启动时间：模块导入时刻就是进程起点，足够回答「这个节点跑了多久」
_PROCESS_STARTED_AT = time.time()
_PROCESS: Optional[psutil.Process] = None
# db 类型/版本、redis 版本不会在运行期变化，缓存一段时间即可，避免每 15s 查一次
_INFO_CACHE_TTL_SECONDS = 300
_db_info_cache: Dict[str, Any] = {"ts": 0.0, "value": {}}
_redis_info_cache: Dict[str, Any] = {"ts": 0.0, "value": {}}


# --------------------------------------------------------------------------- #
# Key helpers
# --------------------------------------------------------------------------- #
def configured_node_id() -> str:
    """环境变量 / 配置里的实例 ID（``NODE_ID``），未配置时返回空串。"""
    # settings 已经读过 .env（pydantic-settings 不会把它注入 os.environ，
    # 所以只读 os.environ 会让 .env 里的 NODE_ID 静默失效）；环境变量兜底留给
    # 直接注入进程环境的部署方式，以及测试里替换 settings 的场景。
    return str(getattr(settings, "NODE_ID", "") or os.environ.get("NODE_ID") or "").strip()


def real_hostname() -> str:
    """进程所在主机的真实 hostname（容器里是容器 ID / Pod 名）。

    它与「实例 ID」是两件事：配了 ``NODE_ID`` 之后，实例 ID 是人起的名字，
    排查时仍需要看真实 hostname 才能对上容器/Pod。
    """
    try:
        return (socket.gethostname() or "").strip() or "unknown"
    except OSError:  # 极端环境（无 hostname）不该让心跳整体失败
        return "unknown"


def instance_hostname() -> str:
    """实例 ID：``NODE_ID`` > ``INSTANCE_NAME``（旧键，保留兼容）> hostname。

    ``NODE_ID`` 用于多实例部署时给每个进程一个稳定、可读的身份（如 ``dev-node``、
    ``api-1``）；它同时是 Redis 键 ``nanzi:instance:{id}`` 的一部分，也是诊断页
    卡片标题、别名键、趋势键的取值来源。
    """
    for candidate in (configured_node_id(), os.environ.get("INSTANCE_NAME"), real_hostname()):
        value = str(candidate or "").strip()
        if value:
            return value
    return "unknown"


def _instance_key(host: str) -> str:
    return f"{INSTANCE_KEY_PREFIX}{host}"


def _series_key(host: str) -> str:
    return f"{INSTANCE_KEY_PREFIX}{host}:series"


def _alias_key(host: str) -> str:
    return f"{INSTANCE_ALIAS_PREFIX}{host}"


def _now_iso(epoch: Optional[float] = None) -> str:
    return datetime.fromtimestamp(epoch if epoch is not None else time.time()).astimezone().isoformat(timespec="seconds")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #
def _detect_runtime_kind() -> str:
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "k8s"
    if Path("/.dockerenv").exists():
        return "docker"
    return "host"


def _detect_pod_ip() -> str:
    """实例内网 IP：优先显式注入的 POD_IP / INSTANCE_IP，否则解析真实 hostname。

    注意不能拿「实例 ID」去解析：配了 ``NODE_ID=dev-node`` 之后它只是一个自定义名字，
    ``/etc/hosts`` 里通常没有，gethostbyname 会失败，页面上的内网 IP 就变成空白。
    """
    for env_key in ("POD_IP", "INSTANCE_IP"):
        value = (os.environ.get(env_key) or "").strip()
        if value:
            return value
    try:
        return socket.gethostbyname(real_hostname())
    except OSError:
        # 解析不出来就留空：页面上显示「—」比显示 127.0.0.1 更诚实，
        # 后者会让人误以为节点只绑了回环地址。
        return ""


async def _collect_db_info() -> Dict[str, str]:
    """数据库类型与版本。类型取配置（而不是探测），版本真查一次后缓存。"""
    now = time.time()
    cached = _db_info_cache["value"]
    if cached and now - float(_db_info_cache["ts"]) < _INFO_CACHE_TTL_SECONDS:
        return dict(cached)

    db_type = "PostgreSQL" if settings.normalized_database_type == "postgresql" else "MySQL"
    version = ""
    try:
        from app.core.orm import engine

        async with engine.connect() as conn:
            if settings.normalized_database_type == "postgresql":
                result = await conn.execute(text("SHOW server_version"))
            else:
                result = await conn.execute(text("SELECT VERSION()"))
            raw = result.scalar()
            if raw:
                version = str(raw).strip()
                # MySQL 返回 "8.0.36-0ubuntu0.22.04.1"，只留主版本号
                if db_type == "MySQL":
                    version = version.split("-")[0]
    except Exception as exc:  # noqa: BLE001 - 版本取不到不该影响心跳
        logger.debug("读取数据库版本失败: %s", exc)
        version = ""

    value = {"db_type": db_type, "db_version": version or "unknown"}
    _db_info_cache.update({"ts": now, "value": value})
    return value


async def _collect_redis_info(client) -> Dict[str, str]:
    now = time.time()
    cached = _redis_info_cache["value"]
    if cached and now - float(_redis_info_cache["ts"]) < _INFO_CACHE_TTL_SECONDS:
        return dict(cached)

    version = "unknown"
    flavor = "Redis"
    try:
        info = await client.info("server")
        version = str(info.get("redis_version") or "unknown")
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 Redis 版本失败: %s", exc)
    try:
        modules = await client.execute_command("MODULE", "LIST")
        names = " ".join(str(item) for row in modules for item in (row if isinstance(row, (list, tuple)) else [row]))
        if "search" in names.lower():
            flavor = "Redis Stack"
    except Exception:  # noqa: BLE001 - 非 Stack 或权限不足时忽略
        pass

    value = {"redis_version": version, "redis_flavor": flavor}
    _redis_info_cache.update({"ts": now, "value": value})
    return value


def _collect_process_metrics() -> Dict[str, Any]:
    global _PROCESS
    out: Dict[str, Any] = {"threads": 0, "open_files": 0}
    try:
        if _PROCESS is None:
            _PROCESS = psutil.Process()
        out["threads"] = _PROCESS.num_threads()
        try:
            out["open_files"] = _PROCESS.num_fds()  # Linux / macOS；Windows 无此 API
        except (AttributeError, NotImplementedError):
            out["open_files"] = len(_PROCESS.open_files())
    except Exception as exc:  # noqa: BLE001
        logger.debug("采集进程指标失败: %s", exc)
    return out


async def _collect_snapshot(client, seq: int) -> Dict[str, str]:
    """采集本实例快照。任何分项失败都只降级为空值，绝不让心跳整体失败。"""
    host = instance_hostname()
    now = time.time()

    try:
        cpu_percent = float(psutil.cpu_percent(interval=None))
    except Exception:  # noqa: BLE001
        cpu_percent = 0.0
    try:
        virtual_mem = psutil.virtual_memory()
        mem_used_mb = int(virtual_mem.used / 1024 / 1024)
        mem_total_mb = int(virtual_mem.total / 1024 / 1024)
    except Exception:  # noqa: BLE001
        mem_used_mb, mem_total_mb = 0, 0
    try:
        disk = psutil.disk_usage("/")
        disk_used_gb = round(disk.used / 1024 / 1024 / 1024, 1)
        disk_total_gb = round(disk.total / 1024 / 1024 / 1024, 1)
    except Exception:  # noqa: BLE001
        disk_used_gb, disk_total_gb = 0.0, 0.0
    try:
        load1, load5, load15 = psutil.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = 0.0

    db_info = await _collect_db_info()
    redis_info = await _collect_redis_info(client)

    # 依赖延迟：真打一次，失败也照样上报（页面靠 ok 字段区分）
    db_latency_ms = -1.0
    db_ok = True
    try:
        from app.core.orm import engine

        started = time.perf_counter()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_latency_ms = round((time.perf_counter() - started) * 1000, 1)
    except Exception:  # noqa: BLE001
        db_ok = False

    redis_latency_ms = -1.0
    redis_ok = True
    try:
        started = time.perf_counter()
        await client.ping()
        redis_latency_ms = round((time.perf_counter() - started) * 1000, 1)
    except Exception:  # noqa: BLE001
        redis_ok = False

    # 集群视角：在线用户数来自共享 Redis，属于「全平台」口径，页面上会标注
    online_users = 0
    try:
        online_users = int(await client.zcard(ONLINE_PRESENCE_ZSET_KEY))
    except Exception:  # noqa: BLE001
        online_users = 0

    sw_metrics = metrics.snapshot()
    # 事件循环延迟是「进程视角」：HTTP 指标全好的时候它也可能已经很难看
    loop_metrics = loop_lag.snapshot()
    proc_metrics = _collect_process_metrics()

    snapshot: Dict[str, Any] = {
        "host": host,
        # 实例 ID（NODE_ID 优先）与真实主机名分开上报：多实例部署时前者是人起的名字，
        # 后者是容器/Pod 的真实身份，排查时两者都要能对上。
        "instance_id": host,
        "hostname": real_hostname(),
        "node_id": configured_node_id(),
        "primary": "true" if settings.TASK_SCHEDULER_ENABLED else "false",
        "runtime_kind": _detect_runtime_kind(),
        "node_name": (os.environ.get("NODE_NAME") or real_hostname()).strip(),
        "pod_ip": _detect_pod_ip(),
        "app_version": (os.environ.get("APP_VERSION") or "dev").strip(),
        "commit": (os.environ.get("GIT_COMMIT") or "").strip(),
        "image": (os.environ.get("IMAGE_TAG") or "").strip(),
        "started_at": _now_iso(_PROCESS_STARTED_AT),
        "uptime_seconds": int(now - _PROCESS_STARTED_AT),
        "cpu_percent": round(cpu_percent, 1),
        "cpu_count": psutil.cpu_count(logical=True) or 0,
        "load1": round(float(load1), 2),
        "load5": round(float(load5), 2),
        "load15": round(float(load15), 2),
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "threads": proc_metrics["threads"],
        "open_files": proc_metrics["open_files"],
        "pid": os.getpid(),
        "python_version": platform.python_version(),
        "db_type": db_info["db_type"],
        "db_version": db_info["db_version"],
        "db_ok": "true" if db_ok else "false",
        "db_latency_ms": db_latency_ms,
        "redis_version": redis_info["redis_version"],
        "redis_flavor": redis_info["redis_flavor"],
        "redis_ok": "true" if redis_ok else "false",
        "redis_latency_ms": redis_latency_ms,
        "online_users": online_users,
        "requests_1m": sw_metrics["requests_1m"],
        "qps": sw_metrics["qps"],
        "p95_ms": sw_metrics["p95_ms"],
        "p99_ms": sw_metrics["p99_ms"],
        "error_rate": sw_metrics["error_rate"],
        "inflight": sw_metrics["inflight"],
        "chat_inflight": sw_metrics["chat_inflight"],
        "loop_lag_p95_ms": loop_metrics["loop_lag_p95_ms"],
        "heartbeat_at": _now_iso(now),
        "heartbeat_epoch": f"{now:.3f}",
        "heartbeat_seq": seq,
    }
    # 全量转字符串：HGETALL 统一按文本处理，读取侧再转回类型
    return {key: str(value) for key, value in snapshot.items()}


# --------------------------------------------------------------------------- #
# 心跳上报
# --------------------------------------------------------------------------- #
class InstanceHeartbeatReporter:
    """后台协程：每 15 秒写快照、每分钟落一个趋势点。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._seq = 0
        self._last_series_ts = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        if not settings.REDIS_ENABLE:
            logger.info("Redis 未启用，跳过实例心跳上报")
            return
        # 预热：psutil.cpu_percent 首次调用恒为 0
        try:
            psutil.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            pass
        self._task = asyncio.create_task(self._run(), name="instance-heartbeat")
        logger.info(
            "实例心跳上报已启动：instance_id=%s hostname=%s source=%s primary=%s",
            instance_hostname(),
            real_hostname(),
            "NODE_ID" if configured_node_id() else ("INSTANCE_NAME" if os.environ.get("INSTANCE_NAME") else "hostname"),
            settings.TASK_SCHEDULER_ENABLED,
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("实例心跳任务退出异常: %s", exc)
        await self._cleanup()

    async def _cleanup(self) -> None:
        """优雅退出：把自己的 HASH 与索引一起摘掉，别让页面等 45 秒才发现节点没了。"""
        client = await get_redis()
        if client is None:
            return
        host = instance_hostname()
        try:
            await client.delete(_instance_key(host))
            await client.srem(INSTANCE_INDEX_KEY, host)
        except Exception as exc:  # noqa: BLE001
            logger.debug("清理实例心跳键失败: %s", exc)

    async def _run(self) -> None:
        # psutil.cpu_percent(interval=None) 报的是「距上次调用」的平均值，紧接着启动
        # 预热调用会得到 0.0 并一直停在第一次心跳上。等 1 秒再采第一拍，代价可以忽略。
        await asyncio.sleep(1)
        while True:
            try:
                await self._beat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("实例心跳写入失败（下次重试）: %s", exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def _beat(self) -> None:
        client = await get_redis()
        if client is None:
            return
        self._seq += 1
        host = instance_hostname()
        snapshot = await _collect_snapshot(client, self._seq)

        key = _instance_key(host)
        pipe = client.pipeline()
        pipe.hset(key, mapping=snapshot)
        pipe.expire(key, INSTANCE_TTL_SECONDS)
        pipe.sadd(INSTANCE_INDEX_KEY, host)
        await pipe.execute()

        await self._maybe_write_series(client, host, snapshot)

    async def _maybe_write_series(self, client, host: str, snapshot: Dict[str, str]) -> None:
        now = time.time()
        if now - self._last_series_ts < SERIES_SAMPLE_INTERVAL_SECONDS:
            return
        self._last_series_ts = now

        bucket = int(now) - int(now) % SERIES_SAMPLE_INTERVAL_SECONDS
        member = "|".join(
            [
                snapshot.get("cpu_percent", "0"),
                snapshot.get("mem_used_mb", "0"),
                snapshot.get("qps", "0"),
                snapshot.get("chat_inflight", "0"),
                snapshot.get("inflight", "0"),
            ]
        )
        key = _series_key(host)
        try:
            pipe = client.pipeline()
            # 同一分钟内重复写入时先清掉该分钟的旧点，避免一个时间戳挂多个 member
            pipe.zremrangebyscore(key, bucket, bucket)
            pipe.zadd(key, {member: bucket})
            # 保留一天的滚动窗口：进程一直活着时 TTL 会被不断续期，必须靠按分数裁剪
            pipe.zremrangebyscore(key, "-inf", bucket - SERIES_TTL_SECONDS)
            pipe.expire(key, SERIES_TTL_SECONDS)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.debug("写入实例趋势点失败: %s", exc)


heartbeat_reporter = InstanceHeartbeatReporter()


# --------------------------------------------------------------------------- #
# 读取 / 别名
# --------------------------------------------------------------------------- #
def _parse_series_point(raw: Any, score: float) -> Dict[str, Any]:
    text_value = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    parts = text_value.split("|")
    while len(parts) < 5:
        parts.append("0")
    return {
        "ts": int(score),
        "cpu": _to_float(parts[0]),
        "mem_mb": _to_int(parts[1]),
        "qps": _to_float(parts[2]),
        "chat_inflight": _to_int(parts[3]),
        "inflight": _to_int(parts[4]),
    }


def _parse_instance(host: str, raw: Dict[str, Any], alias: str, series: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = {
        (key.decode("utf-8") if isinstance(key, bytes) else str(key)): (
            value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
        )
        for key, value in raw.items()
    }
    heartbeat_epoch = _to_float(data.get("heartbeat_epoch"), 0.0)
    age_seconds = max(0, int(time.time() - heartbeat_epoch)) if heartbeat_epoch else INSTANCE_TTL_SECONDS + 1
    online = age_seconds <= INSTANCE_TTL_SECONDS

    return {
        "host": host,
        "alias": alias or "",
        "primary": _to_bool(data.get("primary")),
        "online": online,
        "heartbeat_age_seconds": age_seconds,
        "heartbeat_at": data.get("heartbeat_at", ""),
        "uptime_seconds": _to_int(data.get("uptime_seconds")),
        "started_at": data.get("started_at", ""),
        "version": {
            "app": data.get("app_version", ""),
            "commit": data.get("commit", ""),
            "image": data.get("image", ""),
        },
        "runtime": {
            "kind": data.get("runtime_kind", ""),
            # 实例 ID 由 key 提供；hostname/node_id 是新增采集项，旧快照里没有时
            # 回退到 host，保证升级过程中页面不出现空白。
            "instance_id": data.get("instance_id", "") or host,
            "node_id": data.get("node_id", ""),
            "hostname": data.get("hostname", "") or data.get("node_name", "") or host,
            "node_name": data.get("node_name", ""),
            "pod_ip": data.get("pod_ip", ""),
            "python_version": data.get("python_version", ""),
            "pid": _to_int(data.get("pid")),
            "threads": _to_int(data.get("threads")),
            "open_files": _to_int(data.get("open_files")),
        },
        "resources": {
            "cpu_percent": _to_float(data.get("cpu_percent")),
            "cpu_count": _to_int(data.get("cpu_count")),
            "load": [_to_float(data.get("load1")), _to_float(data.get("load5")), _to_float(data.get("load15"))],
            "mem_used_mb": _to_int(data.get("mem_used_mb")),
            "mem_total_mb": _to_int(data.get("mem_total_mb")),
            "disk_used_gb": _to_float(data.get("disk_used_gb")),
            "disk_total_gb": _to_float(data.get("disk_total_gb")),
        },
        "stack": {
            "db_type": data.get("db_type", ""),
            "db_version": data.get("db_version", ""),
            "redis_version": data.get("redis_version", ""),
            "redis_flavor": data.get("redis_flavor", ""),
        },
        "deps": {
            "db_ok": _to_bool(data.get("db_ok", "true")),
            "db_latency_ms": _to_float(data.get("db_latency_ms")),
            "redis_ok": _to_bool(data.get("redis_ok", "true")),
            "redis_latency_ms": _to_float(data.get("redis_latency_ms")),
        },
        "traffic": {
            "requests_1m": _to_int(data.get("requests_1m")),
            "qps": _to_float(data.get("qps")),
            "p95_ms": _to_float(data.get("p95_ms")),
            "p99_ms": _to_float(data.get("p99_ms")),
            "error_rate": _to_float(data.get("error_rate")),
            "inflight": _to_int(data.get("inflight")),
            "chat_inflight": _to_int(data.get("chat_inflight")),
            "loop_lag_p95_ms": _to_float(data.get("loop_lag_p95_ms")),
        },
        "cluster": {"online_users": _to_int(data.get("online_users"))},
        "series": {
            "cpu": [point["cpu"] for point in series],
            "mem": [point["mem_mb"] for point in series],
            "qps": [point["qps"] for point in series],
        },
    }


def _build_alerts(instances: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    primaries = [item for item in instances if item["primary"]]

    if len(primaries) > 1:
        alerts.append(
            {
                "level": "critical",
                "code": "multi_primary",
                "hosts": [item["host"] for item in primaries],
                "message": (
                    f"检测到 {len(primaries)} 个主节点（"
                    + "、".join(item["alias"] or item["host"] for item in primaries)
                    + "）：它们都会执行定时任务与调度，可能出现重复触发。"
                    "TASK_SCHEDULER_ENABLED=true 只应在主节点上开启。"
                ),
            }
        )
    if len(primaries) == 1 and not primaries[0]["online"]:
        alerts.append(
            {
                "level": "critical",
                "code": "primary_offline",
                "hosts": [primaries[0]["host"]],
                "message": (
                    f"主节点 {primaries[0]['alias'] or primaries[0]['host']} 已离线："
                    "定时任务与调度会停摆，请尽快恢复或把主节点切到其他实例。"
                ),
            }
        )
    if not primaries:
        alerts.append(
            {
                "level": "warning",
                "code": "no_primary",
                "hosts": [],
                "message": "没有实例开启 TASK_SCHEDULER_ENABLED：定时任务与调度不会执行。",
            }
        )

    online_versions: Dict[str, List[str]] = {}
    for item in instances:
        if not item["online"]:
            continue
        version = item["version"]["app"] or "unknown"
        online_versions.setdefault(version, []).append(item["host"])
    if len(online_versions) > 1:
        majority = max(online_versions.items(), key=lambda pair: len(pair[1]))[0]
        drift = [
            {"host": host, "version": version}
            for version, hosts in online_versions.items()
            if version != majority
            for host in hosts
        ]
        alerts.append(
            {
                "level": "warning",
                "code": "version_drift",
                "hosts": [item["host"] for item in drift],
                "message": (
                    "版本与集群基准不一致："
                    + "、".join(f"{item['host']} 为 {item['version']}" for item in drift)
                    + f"，多数节点为 {majority}。建议滚动更新后再观察一次。"
                ),
            }
        )

    for item in instances:
        if not item["online"]:
            continue
        mem_total = item["resources"]["mem_total_mb"]
        if mem_total and item["resources"]["mem_used_mb"] / mem_total >= 0.85:
            alerts.append(
                {
                    "level": "warning",
                    "code": "high_memory",
                    "hosts": [item["host"]],
                    "message": f"{item['alias'] or item['host']} 内存使用已超过 85%，请关注是否有会话或工具常驻内存。",
                }
            )
    return alerts


async def list_instances() -> Dict[str, Any]:
    """聚合所有实例的心跳快照。Redis 不可用时返回空列表 + 告警，而不是抛错。"""
    client = await get_redis()
    if client is None:
        return {
            "instances": [],
            "summary": {"total": 0, "online": 0, "primary": 0, "offline": 0},
            "alerts": [
                {
                    "level": "critical",
                    "code": "redis_unavailable",
                    "hosts": [],
                    "message": "Redis 不可用，读不到任何实例心跳；实例页需要共享 Redis 才能工作。",
                }
            ],
            "intervals": _intervals_payload(),
        }

    try:
        hosts = sorted(str(host) for host in await client.smembers(INSTANCE_INDEX_KEY))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取实例索引失败: %s", exc)
        hosts = []

    instances: List[Dict[str, Any]] = []
    stale: List[str] = []
    if hosts:
        pipe = client.pipeline()
        for host in hosts:
            pipe.hgetall(_instance_key(host))
            pipe.zrange(_series_key(host), -SPARK_POINTS, -1, withscores=True)
            pipe.get(_alias_key(host))
        try:
            raw_results = await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取实例心跳失败: %s", exc)
            raw_results = []

        for index, host in enumerate(hosts):
            base = index * 3
            if base + 2 >= len(raw_results):
                break
            snapshot_raw, series_raw, alias_raw = raw_results[base : base + 3]
            if not snapshot_raw:
                # HASH 已过期（TTL）：索引里还留着 host，顺手清掉
                stale.append(host)
                continue
            alias = alias_raw.decode("utf-8", errors="replace") if isinstance(alias_raw, bytes) else (alias_raw or "")
            series = [_parse_series_point(member, score) for member, score in (series_raw or [])]
            instances.append(_parse_instance(host, snapshot_raw, str(alias), series))

    if stale:
        try:
            await client.srem(INSTANCE_INDEX_KEY, *stale)
        except Exception:  # noqa: BLE001
            pass

    online = [item for item in instances if item["online"]]
    summary = {
        "total": len(instances),
        "online": len(online),
        "primary": len([item for item in instances if item["primary"]]),
        "offline": len(instances) - len(online),
    }
    return {
        "instances": instances,
        "summary": summary,
        "alerts": _build_alerts(instances),
        "intervals": _intervals_payload(),
    }


def _intervals_payload() -> Dict[str, int]:
    return {
        "heartbeat_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "offline_after_seconds": INSTANCE_TTL_SECONDS,
        "series_seconds": SERIES_SAMPLE_INTERVAL_SECONDS,
        "series_retention_seconds": SERIES_TTL_SECONDS,
    }


async def get_instance_series(host: str, *, hours: int = 24) -> Dict[str, Any]:
    """单个实例的趋势。默认给 24 小时，点数超过 MAX_RETURN_POINTS 时按步长抽样。"""
    hours = max(1, min(24, int(hours)))
    client = await get_redis()
    if client is None:
        return {"host": host, "hours": hours, "points": []}

    since = int(time.time()) - hours * 3600
    try:
        rows = await client.zrangebyscore(_series_key(host), since, "+inf", withscores=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取实例趋势失败: %s", exc)
        rows = []

    points = [_parse_series_point(member, score) for member, score in rows]
    if len(points) > MAX_RETURN_POINTS:
        step = max(1, len(points) // MAX_RETURN_POINTS)
        sampled = points[::step]
        if sampled[-1]["ts"] != points[-1]["ts"]:
            sampled.append(points[-1])
        points = sampled
    return {"host": host, "hours": hours, "points": points}


async def set_instance_alias(host: str, alias: str) -> Dict[str, str]:
    """别名单独存一个不带 TTL 的键：心跳过期不影响别名，节点回来还认得出来。"""
    client = await get_redis()
    cleaned = (alias or "").strip()[:ALIAS_MAX_LENGTH]
    if client is None:
        return {"host": host, "alias": cleaned}
    key = _alias_key(host)
    if cleaned:
        await client.set(key, cleaned)
    else:
        await client.delete(key)
    return {"host": host, "alias": cleaned}
