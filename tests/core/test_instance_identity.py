"""实例身份解析（``NODE_ID`` 优先级）与快照字段的单测。

背景（用户需求）：多实例部署时诊断页要一眼看出「这是哪个节点」。容器里的 hostname
是随机串（``80bb99ffd85a``），所以新增 ``NODE_ID``：配置了就优先用它当实例 ID，
没配仍回退 hostname，行为与升级前一致。

守住的约定：

1. 优先级 ``NODE_ID`` > ``INSTANCE_NAME``（旧键，保留兼容）> hostname；
   空串/纯空白等于没配，不能把实例 ID 变成空字符串（否则 Redis 键会退化成
   ``nanzi:instance:``，多个节点互相覆盖）；
2. 实例 ID 与真实 hostname 是两件事：快照里都要有，配了 ``NODE_ID`` 之后
   排查仍然要能对上容器/Pod；
3. 内网 IP 不能拿实例 ID 去解析——``dev-node`` 这类自定义名字在 /etc/hosts 里
   不存在，会解析失败导致页面 IP 空白。
"""

from __future__ import annotations

import socket
from typing import Any, Dict, List

import pytest

from app.services import instance_registry as registry


class _FakeClient:
    """只实现 ``_collect_snapshot`` 会用到的那几个 Redis 命令。"""

    def __init__(self) -> None:
        self.ping_count = 0

    async def zcard(self, key: str) -> int:
        return 0

    async def ping(self) -> bool:
        self.ping_count += 1
        return True

    async def info(self, section: Any = None) -> Dict[str, str]:
        return {"redis_version": "7.4.1"}

    async def execute_command(self, *args: Any) -> List[Any]:
        return [["name", "search", 2]]


@pytest.fixture()
def clean_env(monkeypatch):
    """清掉可能来自开发机的实例标识，让每个用例从「什么都没配」开始。"""
    monkeypatch.setattr(registry.settings, "NODE_ID", None)
    monkeypatch.delenv("NODE_ID", raising=False)
    monkeypatch.delenv("INSTANCE_NAME", raising=False)
    monkeypatch.delenv("POD_IP", raising=False)
    monkeypatch.delenv("INSTANCE_IP", raising=False)
    return monkeypatch


# --------------------------------------------------------------------------- #
# 实例 ID 优先级
# --------------------------------------------------------------------------- #
def test_node_id_from_settings_wins(clean_env):
    """``.env`` 里的 NODE_ID 必须生效：pydantic-settings 不把它注入 os.environ，
    只读环境变量的实现会在真实部署里静默失效。"""
    clean_env.setattr(registry.settings, "NODE_ID", "dev-node")

    assert registry.configured_node_id() == "dev-node"
    assert registry.instance_hostname() == "dev-node"


def test_node_id_from_process_env_wins(clean_env):
    """容器/K8s 直接注入进程环境变量的路径同样要认。"""
    clean_env.setenv("NODE_ID", " api-2 ")

    assert registry.instance_hostname() == "api-2"


def test_blank_node_id_falls_back_to_hostname(clean_env):
    """空串/纯空白等于没配：实例 ID 不能变成空键。"""
    clean_env.setattr(registry.settings, "NODE_ID", "   ")

    assert registry.configured_node_id() == ""
    assert registry.instance_hostname() == socket.gethostname()


def test_legacy_instance_name_still_supported(clean_env):
    """旧部署可能用的是 INSTANCE_NAME，升级后不能失效。"""
    clean_env.setenv("INSTANCE_NAME", "legacy-node")

    assert registry.instance_hostname() == "legacy-node"


def test_node_id_beats_legacy_instance_name(clean_env):
    clean_env.setattr(registry.settings, "NODE_ID", "dev-node")
    clean_env.setenv("INSTANCE_NAME", "legacy-node")

    assert registry.instance_hostname() == "dev-node"


# --------------------------------------------------------------------------- #
# 内网 IP 解析
# --------------------------------------------------------------------------- #
def test_pod_ip_prefers_injected_env(clean_env):
    clean_env.setenv("POD_IP", "10.42.7.31")

    assert registry._detect_pod_ip() == "10.42.7.31"


def test_pod_ip_resolves_real_hostname_not_instance_id(clean_env):
    """改 NODE_ID 之后 IP 不能变空：解析目标必须是真实 hostname。"""
    clean_env.setattr(registry.settings, "NODE_ID", "dev-node")
    resolved: List[str] = []

    def _fake_resolve(host: str) -> str:
        resolved.append(host)
        if host == "dev-node":
            raise OSError("Name or service not known")
        return "172.18.0.2"

    clean_env.setattr(registry.socket, "gethostbyname", _fake_resolve)

    assert registry._detect_pod_ip() == "172.18.0.2"
    assert resolved == [registry.real_hostname()], "不应该拿实例 ID 去解析 IP"


# --------------------------------------------------------------------------- #
# 心跳快照字段
# --------------------------------------------------------------------------- #
async def _snapshot_with_node_id(clean_env, node_id: str) -> Dict[str, str]:
    clean_env.setattr(registry.settings, "NODE_ID", node_id or None)

    async def _db_info() -> Dict[str, str]:
        return {"db_type": "MySQL", "db_version": "8.0.36"}

    async def _redis_info(client) -> Dict[str, str]:
        return {"redis_version": "7.4.1", "redis_flavor": "Redis Stack"}

    clean_env.setattr(registry, "_collect_db_info", _db_info)
    clean_env.setattr(registry, "_collect_redis_info", _redis_info)
    return await registry._collect_snapshot(_FakeClient(), 1)


async def test_snapshot_uses_node_id_as_instance_id(clean_env):
    snapshot = await _snapshot_with_node_id(clean_env, "dev-node")

    assert snapshot["host"] == "dev-node"
    assert snapshot["instance_id"] == "dev-node"
    assert snapshot["node_id"] == "dev-node"
    # 真实主机名仍然要上报：多实例排障时要能对上容器/Pod
    assert snapshot["hostname"] == registry.real_hostname()
    assert snapshot["node_name"] == registry.real_hostname()


async def test_snapshot_without_node_id_stays_identical_to_before(clean_env):
    """未配置 NODE_ID 时，实例 ID 仍是 hostname——升级不改变现有页面的显示。"""
    snapshot = await _snapshot_with_node_id(clean_env, "")

    assert snapshot["host"] == socket.gethostname()
    assert snapshot["instance_id"] == snapshot["host"]
    assert snapshot["node_id"] == ""


def test_parse_instance_exposes_instance_identity():
    """接口层要把实例 ID 与主机名都吐给前端；旧快照（无新字段）要能回退不空白。"""
    raw = {
        "host": "dev-node",
        "primary": "false",
        "runtime_kind": "docker",
        "node_name": "80bb99ffd85a",
        "pod_ip": "172.18.0.2",
        "heartbeat_epoch": "0",
    }
    legacy = registry._parse_instance("dev-node", raw, "", [])
    assert legacy["runtime"]["instance_id"] == "dev-node"
    assert legacy["runtime"]["hostname"] == "80bb99ffd85a"
    assert legacy["runtime"]["node_id"] == ""

    fresh = registry._parse_instance(
        "dev-node",
        {**raw, "instance_id": "dev-node", "hostname": "80bb99ffd85a", "node_id": "dev-node"},
        "",
        [],
    )
    assert fresh["runtime"]["instance_id"] == "dev-node"
    assert fresh["runtime"]["hostname"] == "80bb99ffd85a"
    assert fresh["runtime"]["node_id"] == "dev-node"


# --------------------------------------------------------------------------- #
# 部署模板契约
# --------------------------------------------------------------------------- #
def test_env_templates_document_node_id():
    """模板里必须有 NODE_ID：这是运维唯一会看的地方，只写进代码注释等于没配置。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in ("env.example", "docker/env.example"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "NODE_ID=dev-node" in text, f"{rel} 缺少 NODE_ID 配置项"
        # 模板要讲清它同时是 Redis 键的一部分，否则运维改完不明白页面为什么多了一个节点
        assert "nanzi:instance:{id}" in text, f"{rel} 缺少 NODE_ID 用途说明"


def test_compose_passes_node_id_into_container():
    """容器内没有 .env（.dockerignore 已排除），compose 不传就是静默回落 hostname。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in ("docker/docker-compose.yml", "docker/docker-compose.ai-agent.yml"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "NODE_ID=${NODE_ID:-}" in text, f"{rel} 未注入 NODE_ID"
