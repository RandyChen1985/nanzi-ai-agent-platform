"""实例心跳登记（``app/services/instance_registry.py``）与请求指标的单测。

这些用例刻意不连真实 Redis：心跳写入、聚合读取、告警判定都是纯逻辑 + 一组
Redis 命令，用一个内存版 fake 就能锁定行为，跑起来也不依赖本机环境。

守住的几条约定：

1. 心跳过期（超过 TTL）即判离线，但**别名不能跟着过期**——节点回来要还认得出来；
2. 主从只来自 ``TASK_SCHEDULER_ENABLED``，出现两个主节点必须报 critical，
   这是「本地实例连到生产 Redis」最常见的事故形态；
3. 趋势点每分钟一个、只留 24 小时，裁剪靠 ``ZREMRANGEBYSCORE``（进程常驻时
   TTL 会被不断续期，光靠 EXPIRE 永远删不掉旧点）；
4. 查询趋势的小时数被夹在 1~24，点数上限 240，别让一次请求吐出 1440 个点。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.services import instance_registry as registry


# --------------------------------------------------------------------------- #
# 内存版 Redis：只实现被用到的命令
# --------------------------------------------------------------------------- #
class FakePipeline:
    def __init__(self, client: "FakeRedis") -> None:
        self._client = client
        self._queue: List[Tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _enqueue(*args, **kwargs):
            self._queue.append((name, args, kwargs))
            return self

        return _enqueue

    async def execute(self):
        results = []
        for name, args, kwargs in self._queue:
            results.append(await getattr(self._client, name)(*args, **kwargs))
        self._queue.clear()
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.sets: Dict[str, set] = {}
        self.zsets: Dict[str, Dict[str, float]] = {}
        self.strings: Dict[str, str] = {}
        self.expires: Dict[str, int] = {}
        self.ping_count = 0

    # --- string ---
    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            for store in (self.hashes, self.sets, self.zsets, self.strings):
                if key in store:
                    store.pop(key, None)
                    removed += 1
        return removed

    async def mget(self, keys):
        return [self.strings.get(key) for key in keys]

    # --- hash ---
    async def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return len(mapping or {})

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    # --- set ---
    async def sadd(self, key, *members):
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def srem(self, key, *members):
        bucket = self.sets.get(key, set())
        before = len(bucket)
        bucket.difference_update(members)
        return before - len(bucket)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    # --- zset ---
    async def zadd(self, key, mapping):
        bucket = self.zsets.setdefault(key, {})
        bucket.update({member: float(score) for member, score in mapping.items()})
        return len(mapping)

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def _sorted(self, key) -> List[Tuple[str, float]]:
        return sorted(self.zsets.get(key, {}).items(), key=lambda pair: pair[1])

    async def zrange(self, key, start, end, withscores=False):
        items = self._sorted(key)
        # Redis 的 start/end 是闭区间且支持负索引，与 Python 切片语义一致
        if end == -1:
            sliced = items[start:]
        else:
            sliced = items[start : end + 1]
        return [(member, score) for member, score in sliced] if withscores else [member for member, _ in sliced]

    async def zrangebyscore(self, key, minimum, maximum, withscores=False):
        low = float("-inf") if minimum in ("-inf", b"-inf") else float(minimum)
        high = float("inf") if maximum in ("+inf", b"+inf") else float(maximum)
        items = [(member, score) for member, score in self._sorted(key) if low <= score <= high]
        return items if withscores else [member for member, _ in items]

    async def zremrangebyscore(self, key, minimum, maximum):
        low = float("-inf") if minimum in ("-inf", b"-inf") else float(minimum)
        high = float("inf") if maximum in ("+inf", b"+inf") else float(maximum)
        bucket = self.zsets.get(key, {})
        doomed = [member for member, score in bucket.items() if low <= score <= high]
        for member in doomed:
            bucket.pop(member, None)
        return len(doomed)

    async def expire(self, key, ttl):
        self.expires[key] = ttl
        return True

    async def ping(self):
        self.ping_count += 1
        return True

    async def info(self, section=None):
        return {"redis_version": "7.4.1"}

    async def execute_command(self, *args):
        return [["name", "search", 2], ["name", "timeseries", 1]]

    def pipeline(self):
        return FakePipeline(self)


@pytest.fixture()
def fake_redis(monkeypatch) -> FakeRedis:
    client = FakeRedis()

    async def _getter():
        return client

    monkeypatch.setattr(registry, "get_redis", _getter)
    return client


def _snapshot(host: str, *, primary: bool = False, age_seconds: int = 3, **overrides: Any) -> Dict[str, str]:
    now = time.time()
    data: Dict[str, Any] = {
        "host": host,
        "primary": "true" if primary else "false",
        "runtime_kind": "k8s",
        "node_name": "k8s-node-a",
        "pod_ip": "10.42.7.31",
        "app_version": "v1.0.0-rc.48",
        "commit": "a91f3ce",
        "image": "nanzi/ai-agent:1.0.0-rc.48",
        "started_at": "2026-11-04 14:30:09",
        "uptime_seconds": 3600,
        "cpu_percent": 41.2,
        "cpu_count": 8,
        "load1": 2.4,
        "load5": 2.1,
        "load15": 1.8,
        "mem_used_mb": 1863,
        "mem_total_mb": 4096,
        "disk_used_gb": 18.2,
        "disk_total_gb": 60,
        "threads": 64,
        "open_files": 486,
        "pid": 128,
        "python_version": "3.11.9",
        "db_type": "MySQL",
        "db_version": "8.0.36",
        "db_ok": "true",
        "db_latency_ms": 2.1,
        "redis_version": "7.2.4",
        "redis_flavor": "Redis Stack",
        "redis_ok": "true",
        "redis_latency_ms": 1.3,
        "online_users": 12,
        "requests_1m": 2070,
        "qps": 34.5,
        "p95_ms": 412,
        "p99_ms": 640,
        "error_rate": 0.12,
        "inflight": 7,
        "chat_inflight": 3,
        "loop_lag_p95_ms": 12.5,
        "heartbeat_at": registry._now_iso(now),
        "heartbeat_epoch": f"{now - age_seconds:.3f}",
        "heartbeat_seq": 9,
    }
    data.update(overrides)
    return {key: str(value) for key, value in data.items()}


# --------------------------------------------------------------------------- #
# 单实例解析
# --------------------------------------------------------------------------- #
def test_parse_instance_maps_types_and_computes_online(fake_redis):
    item = registry._parse_instance("node-a", _snapshot("node-a"), "生产节点 A", [])

    assert item["host"] == "node-a"
    assert item["alias"] == "生产节点 A"
    assert item["primary"] is False
    assert item["online"] is True
    assert item["resources"]["cpu_percent"] == 41.2
    assert item["resources"]["mem_used_mb"] == 1863
    assert item["stack"] == {
        "db_type": "MySQL",
        "db_version": "8.0.36",
        "redis_version": "7.2.4",
        "redis_flavor": "Redis Stack",
    }
    assert item["traffic"]["qps"] == 34.5
    assert item["traffic"]["chat_inflight"] == 3
    # 事件循环延迟：HTTP 指标全绿时它可能是唯一的坏消息，必须一路透传到页面
    assert item["traffic"]["loop_lag_p95_ms"] == 12.5


def test_parse_instance_marks_stale_heartbeat_offline(fake_redis):
    """心跳超过 TTL 就是离线：页面不能因为 HASH 还没被删就显示在线。"""
    stale = registry._parse_instance("node-a", _snapshot("node-a", age_seconds=registry.INSTANCE_TTL_SECONDS + 5), "", [])
    assert stale["online"] is False
    assert stale["heartbeat_age_seconds"] > registry.INSTANCE_TTL_SECONDS


def test_parse_series_point_tolerates_short_member(fake_redis):
    point = registry._parse_series_point("40.5|1863", 1_700_000_000)
    assert point["cpu"] == 40.5
    assert point["mem_mb"] == 1863
    assert point["qps"] == 0
    assert point["chat_inflight"] == 0


# --------------------------------------------------------------------------- #
# 告警
# --------------------------------------------------------------------------- #
def _node(host: str, *, primary: bool, version: str = "v1.0.0-rc.48", mem_percent: int = 45) -> Dict[str, Any]:
    return {
        "host": host,
        "alias": "",
        "primary": primary,
        "online": True,
        "version": {"app": version, "commit": "", "image": ""},
        "resources": {"mem_used_mb": mem_percent * 10, "mem_total_mb": 1000},
    }


def test_two_primaries_raise_critical_alert():
    alerts = registry._build_alerts([_node("a", primary=True), _node("b", primary=True)])
    codes = [alert["code"] for alert in alerts]
    assert "multi_primary" in codes
    multi = next(alert for alert in alerts if alert["code"] == "multi_primary")
    assert multi["level"] == "critical"
    assert set(multi["hosts"]) == {"a", "b"}
    assert "TASK_SCHEDULER_ENABLED" in multi["message"]


def test_no_primary_warns():
    alerts = registry._build_alerts([_node("a", primary=False)])
    assert [alert["code"] for alert in alerts] == ["no_primary"]
    assert alerts[0]["level"] == "warning"


def test_primary_offline_is_critical():
    primary = _node("a", primary=True)
    primary["online"] = False
    alerts = registry._build_alerts([primary, _node("b", primary=False)])
    codes = [alert["code"] for alert in alerts]
    assert "primary_offline" in codes
    assert all(alert["level"] == "critical" for alert in alerts if alert["code"] == "primary_offline")


def test_version_drift_flags_minority_node():
    alerts = registry._build_alerts(
        [
            _node("a", primary=True, version="v1.0.0-rc.48"),
            _node("b", primary=False, version="v1.0.0-rc.48"),
            _node("c", primary=False, version="v1.0.0-rc.47"),
        ]
    )
    drift = [alert for alert in alerts if alert["code"] == "version_drift"]
    assert drift and drift[0]["hosts"] == ["c"]
    assert "v1.0.0-rc.47" in drift[0]["message"]


def test_identical_versions_produce_no_drift_alert():
    alerts = registry._build_alerts([_node("a", primary=True), _node("b", primary=False)])
    assert not [alert for alert in alerts if alert["code"] == "version_drift"]


def test_high_memory_alert_only_for_online_nodes():
    alert_list = registry._build_alerts([_node("a", primary=True, mem_percent=90)])
    assert "high_memory" in [alert["code"] for alert in alert_list]

    offline = _node("b", primary=False, mem_percent=90)
    offline["online"] = False
    offline_alerts = registry._build_alerts([_node("a", primary=True), offline])
    assert "high_memory" not in [alert["code"] for alert in offline_alerts]


# --------------------------------------------------------------------------- #
# 聚合读取
# --------------------------------------------------------------------------- #
async def test_list_instances_aggregates_hash_alias_and_spark(fake_redis):
    fake_redis.hashes[registry._instance_key("node-a")] = _snapshot("node-a", primary=True)
    fake_redis.sets[registry.INSTANCE_INDEX_KEY] = {"node-a"}
    fake_redis.strings[registry._alias_key("node-a")] = "生产节点 A"
    for index in range(3):
        fake_redis.zsets[registry._series_key("node-a")] = fake_redis.zsets.get(registry._series_key("node-a"), {})
        fake_redis.zsets[registry._series_key("node-a")][f"{30 + index}|1800|20|2|5"] = 1_700_000_000 + index * 60

    result = await registry.list_instances()

    assert result["summary"] == {"total": 1, "online": 1, "primary": 1, "offline": 0}
    assert result["instances"][0]["alias"] == "生产节点 A"
    assert result["instances"][0]["series"]["cpu"] == [30, 31, 32]
    assert result["intervals"]["heartbeat_seconds"] == registry.HEARTBEAT_INTERVAL_SECONDS
    assert result["intervals"]["series_retention_seconds"] == 24 * 3600


async def test_list_instances_drops_expired_hosts_from_index(fake_redis):
    """HASH 到期被 Redis 删掉后，索引里的 host 必须一并清掉，否则页面会留幽灵条目。"""
    fake_redis.sets[registry.INSTANCE_INDEX_KEY] = {"node-a", "node-gone"}
    fake_redis.hashes[registry._instance_key("node-a")] = _snapshot("node-a", primary=True)

    result = await registry.list_instances()

    assert [item["host"] for item in result["instances"]] == ["node-a"]
    assert fake_redis.sets[registry.INSTANCE_INDEX_KEY] == {"node-a"}


async def test_list_instances_without_redis_returns_actionable_alert(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(registry, "get_redis", _no_redis)
    result = await registry.list_instances()

    assert result["instances"] == []
    assert [alert["code"] for alert in result["alerts"]] == ["redis_unavailable"]


# --------------------------------------------------------------------------- #
# 趋势
# --------------------------------------------------------------------------- #
async def test_get_instance_series_clamps_hours_and_samples(fake_redis):
    key = registry._series_key("node-a")
    base = int(time.time()) - 3000
    for index in range(400):
        fake_redis.zsets.setdefault(key, {})[f"{20 + index % 7}|1500|5|1|2"] = base + index * 7

    result = await registry.get_instance_series("node-a", hours=99)

    assert result["hours"] == 24  # 被夹到上限
    assert len(result["points"]) <= registry.MAX_RETURN_POINTS + 1
    assert result["points"] == sorted(result["points"], key=lambda point: point["ts"])


async def test_get_instance_series_keeps_last_point_when_sampling(fake_redis):
    key = registry._series_key("node-a")
    base = int(time.time()) - 4000
    for index in range(500):
        fake_redis.zsets.setdefault(key, {})[f"{10 + index % 5}|1200|3|0|1"] = base + index * 5

    result = await registry.get_instance_series("node-a", hours=24)
    # 抽样之后仍要保留最新时间点，否则图上最后一个点会「停在过去」
    assert result["points"][-1]["ts"] == max(score for score in fake_redis.zsets[key].values())


async def test_get_instance_series_filters_out_old_points(fake_redis):
    key = registry._series_key("node-a")
    now = int(time.time())
    fake_redis.zsets[key] = {
        "10|100|1|0|0": now - 25 * 3600,  # 超过 24 小时，不应返回
        "20|200|2|0|0": now - 3600,
    }

    result = await registry.get_instance_series("node-a", hours=24)
    assert len(result["points"]) == 1
    assert result["points"][0]["cpu"] == 20


# --------------------------------------------------------------------------- #
# 别名
# --------------------------------------------------------------------------- #
async def test_set_alias_writes_without_ttl(fake_redis):
    result = await registry.set_instance_alias("node-a", "  生产节点 A  ")

    assert result == {"host": "node-a", "alias": "生产节点 A"}
    assert fake_redis.strings[registry._alias_key("node-a")] == "生产节点 A"
    # 别名不随心跳过期：这个键上不应该出现任何 TTL
    assert registry._alias_key("node-a") not in fake_redis.expires


async def test_empty_alias_clears_key(fake_redis):
    fake_redis.strings[registry._alias_key("node-a")] = "旧别名"
    await registry.set_instance_alias("node-a", "   ")
    assert registry._alias_key("node-a") not in fake_redis.strings


async def test_alias_is_truncated_to_max_length(fake_redis):
    long_alias = "生" * (registry.ALIAS_MAX_LENGTH + 10)
    result = await registry.set_instance_alias("node-a", long_alias)
    assert len(result["alias"]) == registry.ALIAS_MAX_LENGTH


# --------------------------------------------------------------------------- #
# 心跳写入
# --------------------------------------------------------------------------- #
async def test_heartbeat_writes_hash_with_ttl_and_index(fake_redis):
    reporter = registry.InstanceHeartbeatReporter()

    async def _collect(client, seq):
        return _snapshot(registry.instance_hostname(), primary=True)

    original = registry._collect_snapshot
    registry._collect_snapshot = _collect  # type: ignore[assignment]
    try:
        await reporter._beat()
    finally:
        registry._collect_snapshot = original  # type: ignore[assignment]

    host = registry.instance_hostname()
    assert fake_redis.hashes[registry._instance_key(host)]["primary"] == "true"
    assert fake_redis.expires[registry._instance_key(host)] == registry.INSTANCE_TTL_SECONDS
    assert host in fake_redis.sets[registry.INSTANCE_INDEX_KEY]
    # 首次心跳就应该落一个趋势点
    assert len(fake_redis.zsets[registry._series_key(host)]) == 1


async def test_series_point_is_written_once_per_minute_and_trimmed(fake_redis):
    reporter = registry.InstanceHeartbeatReporter()
    host = "node-a"
    key = registry._series_key(host)
    old_bucket = int(time.time()) - registry.SERIES_TTL_SECONDS - 600
    fake_redis.zsets[key] = {"1|1|1|0|0": old_bucket}

    snapshot = _snapshot(host)
    await reporter._maybe_write_series(fake_redis, host, snapshot)
    await reporter._maybe_write_series(fake_redis, host, snapshot)  # 同一分钟内第二次应被跳过

    assert len(fake_redis.zsets[key]) == 1
    assert fake_redis.expires[key] == registry.SERIES_TTL_SECONDS
    # 超过一天的旧点被裁掉：进程常驻时 TTL 会被续期，只能靠按分数裁剪
    assert old_bucket not in fake_redis.zsets[key].values()


async def test_stop_cleans_own_keys(fake_redis):
    host = registry.instance_hostname()
    fake_redis.hashes[registry._instance_key(host)] = _snapshot(host)
    fake_redis.sets[registry.INSTANCE_INDEX_KEY] = {host}

    await registry.InstanceHeartbeatReporter().stop()

    assert registry._instance_key(host) not in fake_redis.hashes
    assert host not in fake_redis.sets[registry.INSTANCE_INDEX_KEY]


# --------------------------------------------------------------------------- #
# 请求指标
# --------------------------------------------------------------------------- #
def test_request_metrics_snapshot_counts_qps_and_errors():
    from app.core.request_metrics import RequestMetrics

    metrics = RequestMetrics()
    metrics.begin("api")
    metrics.end("api", 100.0, 200)
    metrics.begin("api")
    metrics.end("api", 300.0, 500)
    metrics.begin("chat")
    metrics.end("chat", 50.0, 200)

    snapshot = metrics.snapshot()
    assert snapshot["requests_1m"] == 3
    assert snapshot["qps"] == pytest.approx(0.05, abs=0.01)
    assert snapshot["p95_ms"] >= 100
    assert snapshot["error_rate"] == pytest.approx(33.33, abs=0.02)
    assert snapshot["inflight"] == 0


def test_request_metrics_tracks_inflight_by_bucket():
    from app.core.request_metrics import RequestMetrics

    metrics = RequestMetrics()
    metrics.begin("chat")
    metrics.begin("chat")
    metrics.begin("api")

    snapshot = metrics.snapshot()
    assert snapshot["inflight"] == 3
    assert snapshot["chat_inflight"] == 2


def test_request_metrics_drops_samples_outside_window():
    from app.core.request_metrics import RequestMetrics

    metrics = RequestMetrics()
    stale = time.time() - 120
    metrics._samples.append((stale, 50.0, 200))  # 直接塞一条过期样本
    metrics.begin("api")
    metrics.end("api", 10.0, 200)

    assert metrics.snapshot()["requests_1m"] == 1


def test_classify_path_separates_chat_from_api():
    """「对话流进行中」只认真正的流式入口。

    这里踩过一个坑：前缀曾写成 ``/api/portal/chat``，而门户对话实际走
    ``/api/v1/chat/conversation/{id}/stream-events``，于是这个指标恒为 0——
    页面看着像「没人聊天」，其实一次都没统计到。同前缀下的历史、反馈这类
    短请求也不该算进「对话流」。
    """
    from app.core.request_metrics import API_BUCKET, CHAT_BUCKET, classify_path

    assert classify_path("/api/v1/chat/conversation/abc/stream-events") == CHAT_BUCKET
    assert classify_path("/api/v1/chat/code-executions/stream") == CHAT_BUCKET
    assert classify_path("/api/v1/chat/history") == API_BUCKET
    assert classify_path("/api/portal/chat/intent") == API_BUCKET
    assert classify_path("/api/portal/system/instances") == API_BUCKET
