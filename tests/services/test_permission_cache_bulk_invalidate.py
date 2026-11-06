"""批量清权限缓存：SCAN 次数必须是常数，不能随用户数线性增长。

**背景（真实故障）**：某角色有 1000+ 成员，管理员改完这个角色的权限点保存，
页面「卡死」。数据其实已经落库（``update_role_permissions`` 先 ``commit``），
但紧接着清缓存是**逐用户串行**的：

    for uid in user_ids:
        await self._invalidate_user_cache(uid)

每个用户要付 2 次 ``SCAN``，而 ``SCAN MATCH`` 的成本是 **O(keyspace)**、
与 pattern 命中几个键无关。1000 个用户 = 2000 次全库遍历，实测：

- keyspace 6.7k → 单次 SCAN 25ms → 仅 SCAN 就 25s/1000 用户；
- keyspace 47k → 单次 SCAN 113ms → 113s/1000 用户；
- 本机整体串行实测 1000 用户 **46.9 秒**（Redis 在本地、无网络 RTT）。

本文件锁住三件事：

1. 批量清理覆盖的键集合必须与逐用户清理**完全一致**——优化不能顺手漏掉某类键，
   漏了就是「改完权限 1 小时内不生效」的静默 bug；
2. 无论批里有几个用户，SCAN 只走固定次数（2 次），DB 只查 1 次；
3. 保存角色权限 / 角色级禁用项的调用点不得再退回逐用户串行。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.permission_service import PermissionService


class _FakeRedis:
    """记录删除与 SCAN 的最小 Redis 桩，用来校验「删对了」与「扫了几次」。"""

    def __init__(self, keys=()):
        self.store = {str(k): "1" for k in keys}
        self.deleted: list[str] = []
        self.scan_patterns: list[str] = []

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            self.deleted.append(key)
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed

    def scan_iter(self, match=None, count=None):
        # 一次调用 = 一次全库遍历，与命中数无关，这里如实记录
        self.scan_patterns.append(match)
        snapshot = list(self.store)

        async def _gen():
            for key in snapshot:
                if match is None or fnmatch.fnmatch(key, match):
                    yield key

        return _gen()


def _all_keys_for(uid: int) -> list[str]:
    """单个用户名下**全部**会被 ``_invalidate_user_cache`` 清掉的键（11 类）。"""
    return [
        # v7 全局键（agent scope 为 "-"）与智能体作用域键族
        f"sys:auth:permissions:v7:user:{uid}:agent:-",
        f"sys:auth:permissions:v7:user:{uid}:agent:agent-a",
        f"sys:auth:permissions:v7:user:{uid}:agent:agent-b",
        # 历史版本键
        f"sys:auth:permissions:v2:user:{uid}",
        f"sys:auth:permissions:v4:user:{uid}",
        f"sys:auth:permissions:v5:user:{uid}",
        f"sys:auth:permissions:v6:user:{uid}",
        # 数据集菜单 + 数据门户导航
        f"agent:dataset_menu:{uid}",
        f"agent:dataset_navigation:{uid}",
        f"agent:dataset_navigation_click_rank:{uid}",
        f"agent:dataset_navigation_click_meta:{uid}",
        # 最近提问键族
        f"agent:dataset_navigation_recent_questions:{uid}:questions:abc123",
        f"agent:dataset_navigation_recent_questions:{uid}:dashboard:def456",
    ]


def _service_with(redis) -> PermissionService:
    service = PermissionService(AsyncMock())
    service._redis = redis
    return service


@pytest.fixture
def fake_db_with_hashes():
    """``_fetch_api_key_hashes`` 依赖 ``self.db.execute``，这里返回 (uid, hash) 行。"""

    def _build(hashes: dict[int, str]):
        db = AsyncMock()
        result = AsyncMock()
        result.all = lambda: [(uid, h) for uid, h in hashes.items()]
        db.execute = AsyncMock(return_value=result)
        return db

    return _build


@pytest.mark.asyncio
async def test_bulk_invalidate_covers_every_key_single_path_covers(fake_db_with_hashes):
    """覆盖性：批量清掉的键集合，必须与逐用户清完全一致。

    少清任何一类都是生产事故——比如漏了 ``...:agent:*`` 族，某个智能体里刚取消的
    禁用会在 TTL（1 小时）内继续生效，而且不会再有第二次失效来纠正它。
    """
    targets = [101, 102, 103]
    bystander = 999

    keys = [k for uid in targets for k in _all_keys_for(uid)]
    keys += _all_keys_for(bystander)
    keys += [
        "agent:dataset_navigation_recent_questions:admin:questions:zzz",
        "agent:dataset_navigation_recent_questions:anon:questions:zzz",
        "some:unrelated:key",
        "ai_agent_users:whatever",
    ]

    redis = _FakeRedis(keys)
    db = fake_db_with_hashes({101: "h101", 102: "h102", 103: "h103"})
    service = PermissionService(db)
    service._redis = redis

    await service.invalidate_cached_permissions_for_users(targets)

    remaining = set(redis.store)
    missing = [k for uid in targets for k in _all_keys_for(uid) if k in remaining]
    assert not missing, f"批量清理漏掉了这些键：{missing}"

    # 旁观者与无关键必须原样保留：多删会造成别人（甚至 admin）被无谓地重建缓存
    for key in _all_keys_for(bystander):
        assert key in remaining, f"误删了无关用户的键：{key}"
    assert "agent:dataset_navigation_recent_questions:admin:questions:zzz" in remaining
    assert "agent:dataset_navigation_recent_questions:anon:questions:zzz" in remaining
    assert "some:unrelated:key" in remaining
    assert "ai_agent_users:whatever" in remaining

    # api_key 认证缓存也要清：角色权限变了，认证缓存里的 role 必须立即失效
    assert "auth:api_key:h101" in redis.deleted
    assert "auth:api_key:h102" in redis.deleted
    assert "auth:api_key:h103" in redis.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("user_count", [2, 10, 200])
async def test_bulk_invalidate_scans_constant_times(fake_db_with_hashes, user_count):
    """SCAN 次数必须是常数，与批内用户数无关。

    这是本次故障的根因：旧实现走并发版 ``Semaphore(16)`` 虽然把墙钟时间压到
    1/7，但 SCAN 总次数仍是 ``2N``，Redis 侧 CPU 与 keyspace 遍历量没变。
    """
    targets = list(range(1, user_count + 1))
    redis = _FakeRedis([k for uid in targets for k in _all_keys_for(uid)])
    db = fake_db_with_hashes({uid: f"h{uid}" for uid in targets})
    service = PermissionService(db)
    service._redis = redis

    await service.invalidate_cached_permissions_for_users(targets)

    assert len(redis.scan_patterns) == 2, (
        f"{user_count} 个用户扫了 {len(redis.scan_patterns)} 次 SCAN，"
        f"应恒为 2 次（智能体作用域键族 + 最近提问键族）"
    )
    # 该删的一个都不能少
    assert all(k not in redis.store for uid in targets for k in _all_keys_for(uid))


@pytest.mark.asyncio
async def test_bulk_invalidate_fetches_api_key_hashes_once(fake_db_with_hashes):
    """api_key_hash 必须一条 IN 查询取完，而不是每人一次 session.get。

    单用户路径里这一步藏在 ``AuthService.invalidate_user_auth_cache`` 内部；
    批量场景沿用就会变成 1000 次往返，单这一项实测就超过 1 秒。
    """
    targets = [11, 12, 13, 14, 15]
    redis = _FakeRedis([k for uid in targets for k in _all_keys_for(uid)])
    db = fake_db_with_hashes({uid: f"h{uid}" for uid in targets})
    service = PermissionService(db)
    service._redis = redis

    await service.invalidate_cached_permissions_for_users(targets)

    assert db.execute.await_count == 1, "api_key_hash 应一条查询取完"
    for uid in targets:
        assert f"auth:api_key:h{uid}" in redis.deleted


@pytest.mark.asyncio
async def test_bulk_invalidate_single_user_keeps_single_path():
    """单个用户仍走原路径：语义与副作用（含 auth 缓存、导航缓存）一字不改。"""
    redis = AsyncMock()

    async def _empty_scan(*_a, **_k):
        for _ in ():
            yield ""

    redis.scan_iter = _empty_scan
    service = _service_with(redis)

    with patch(
        "app.services.auth_service.AuthService.invalidate_user_auth_cache",
        new_callable=AsyncMock,
    ) as invalidate_auth, patch(
        "app.services.ai.config.AgentConfigProvider.invalidate_dataset_menu_cache",
        new_callable=AsyncMock,
    ):
        await service.invalidate_cached_permissions_for_users([42])

    invalidate_auth.assert_awaited_once()
    assert invalidate_auth.await_args.args[0] == 42
    redis.delete.assert_any_await("sys:auth:permissions:v4:user:42")


@pytest.mark.asyncio
async def test_bulk_invalidate_empty_and_duplicate_ids(fake_db_with_hashes):
    """空输入不报错；重复 id 去重后只查一次、只清一遍。"""
    redis = _FakeRedis([])
    service = _service_with(redis)
    await service.invalidate_cached_permissions_for_users([])
    assert redis.scan_patterns == []
    assert redis.deleted == []

    redis2 = _FakeRedis([k for uid in (7, 8) for k in _all_keys_for(uid)])
    db = fake_db_with_hashes({7: "h7", 8: "h8"})
    service2 = PermissionService(db)
    service2._redis = redis2
    await service2.invalidate_cached_permissions_for_users([7, 7, 8, 8, None])

    assert len(redis2.scan_patterns) == 2
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_bulk_invalidate_survives_redis_failure(fake_db_with_hashes):
    """Redis 抖动不能把「保存角色权限」整个打断：数据已 commit，缓存清理失败只记日志。"""
    redis = AsyncMock()
    redis.delete = AsyncMock(side_effect=RuntimeError("redis down"))

    def _boom(*_a, **_k):
        raise RuntimeError("redis down")

    redis.scan_iter = _boom
    db = fake_db_with_hashes({1: "h1", 2: "h2", 3: "h3"})
    service = PermissionService(db)
    service._redis = redis

    cleared = {"called": 0}

    with patch(
        "app.services.ai.skills.policy.clear_forbidden_skill_cache",
        lambda: cleared.__setitem__("called", cleared["called"] + 1),
    ):
        await service.invalidate_cached_permissions_for_users([1, 2, 3])

    # 关键：不抛异常（否则端点 500），且进程内技能缓存仍然被清
    assert cleared["called"] == 1


def test_clear_forbidden_skill_cache_called_once_for_whole_batch():
    """进程内缓存只需清一次；旧实现每个用户清一遍，1000 人就是 1000 次空调用。"""
    source = Path(__file__).resolve().parents[2].joinpath(
        "app/services/permission_service.py"
    ).read_text(encoding="utf-8")

    bulk_start = source.index("async def _invalidate_user_caches_bulk")
    # 注意用带 "(self" 的签名：不带的话 `async def _invalidate_user_cache` 会匹配到
    # 批量方法自身（它叫 ...caches_bulk），切出空串导致断言恒真。
    bulk_end = source.index("async def _invalidate_user_cache(self", bulk_start)
    bulk_body = source[bulk_start:bulk_end]
    # 数「调用」而不是「出现」：import 行里也有这个名字
    assert bulk_body.count("clear_forbidden_skill_cache()") == 1


def test_role_permission_save_does_not_iterate_users_serially():
    """保存角色权限不得再逐用户 ``await _invalidate_user_cache``。

    这条锁的是**调用点**：批量实现再好，调用点写回 for 循环就白改。
    """
    source = Path(__file__).resolve().parents[2].joinpath(
        "app/services/permission_service.py"
    ).read_text(encoding="utf-8")

    start = source.index("async def update_role_permissions")
    end = source.index("async def purge_all_permission_cache", start)
    body = source[start:end]

    assert "invalidate_cached_permissions_for_users" in body
    assert "for uid in user_ids:" not in body, (
        "保存角色权限又退回逐用户串行清理了——1000 人角色会卡死"
    )


def test_role_cache_invalidate_does_not_iterate_users_serially():
    """角色级禁用项（智能体作用域）变更同理：1000 人角色同样会卡死。"""
    source = Path(__file__).resolve().parents[2].joinpath(
        "app/services/permission_service.py"
    ).read_text(encoding="utf-8")

    start = source.index("async def _invalidate_role_cache")
    end = source.index("async def get_users_with_element_permission", start)
    body = source[start:end]

    assert "invalidate_cached_permissions_for_users" in body
    assert "_invalidate_user_cache(uid)" not in body


def _endpoint_body(source: str, func_name: str) -> str:
    start = source.index(f"async def {func_name}")
    tail = source[start:]
    candidates = [
        idx for marker in ("\n@router", "\n\ndef ", "\n\n@")
        if (idx := tail.find(marker, 1)) != -1
    ]
    end = start + (min(candidates) if candidates else len(tail))
    return source[start:end]


@pytest.mark.parametrize(
    "func_name", ["add_metadata_dataset_permissions", "delete_metadata_dataset_permission"]
)
def test_dataset_permission_endpoints_do_not_revalidate_menu_cache_per_user(func_name):
    """数据集授权变更端点不得在批量清缓存之后**再逐用户清一遍**菜单缓存。

    ``invalidate_cached_permissions_for_users`` 已经覆盖了 ``agent:dataset_menu:{uid}``
    与全部数据门户导航键（见 ``_delete_exact_user_cache_keys``）。紧随其后的
    逐用户 ``invalidate_dataset_menu_cache`` 删的是同一批键，代价却是每人一次
    全库 SCAN：全员授权时 1000 用户 = 1000 次 SCAN，Redis 服务端照样要串行处理
    几十秒（50 并发只是让客户端等待重叠，遍历总量没变）。
    """
    source = Path(__file__).resolve().parents[2].joinpath(
        "app/api/portal/endpoints/metadata.py"
    ).read_text(encoding="utf-8")
    body = _endpoint_body(source, func_name)

    assert "invalidate_cached_permissions_for_users" in body
    assert "invalidate_dataset_menu_cache" not in body, (
        f"{func_name} 又在批量清缓存后逐用户清菜单缓存了——同一批键，多付 N 次 SCAN"
    )


@pytest.mark.asyncio
async def test_bulk_path_is_key_set_identical_to_single_path():
    """**等价性硬证明**：用真实实现分别跑两条路径，比较删除的键集合。

    前面的覆盖性用例是「手写期望键列表」，只能证明批量删了我以为该删的；这条用
    **未改动的真实实现**（``_invalidate_user_cache`` + ``AgentConfigProvider`` +
    ``DatasetNavigationService`` + ``AuthService``）跑一遍单用户路径，把两边
    ``delete`` 过的键集合直接比对。集合必须**完全相等**：

    - 批量少了某个键 → 该用户对应缓存不失效（改完权限 1 小时内不生效）；
    - 批量多了某个键 → 多删无关缓存（可容忍但说明逻辑走偏）。

    只 patch 三处 ``get_redis`` 让它返回同一个 fake 实例，业务实现一行不动。
    """
    targets = [201, 202, 203]

    def _seed():
        return _FakeRedis([k for uid in targets for k in _all_keys_for(uid)])

    async def _fake_session_get(_cls, _db):
        session = AsyncMock()
        session.get = AsyncMock(
            side_effect=lambda _model, uid: MagicMock(api_key_hash=f"h{uid}")
        )
        session.close = AsyncMock()
        return session, False

    # --- A. 单用户路径（逐个调 _invalidate_user_cache）---
    redis_single = _seed()
    service_single = PermissionService(AsyncMock())
    service_single._redis = redis_single

    with patch("app.core.redis.get_redis", AsyncMock(return_value=redis_single)), patch(
        "app.services.dataset_navigation_service.get_redis", AsyncMock(return_value=redis_single)
    ), patch(
        "app.services.auth_service.get_redis", AsyncMock(return_value=redis_single)
    ), patch(
        "app.services.auth_service.AuthService._get_session",
        classmethod(lambda cls, db=None: _fake_session_get(cls, db)),
    ), patch(
        "app.services.ai.skills.policy.clear_forbidden_skill_cache", lambda: None
    ):
        for uid in targets:
            await service_single._invalidate_user_cache(uid)

    # --- B. 批量路径 ---
    redis_bulk = _seed()
    db = AsyncMock()
    result = AsyncMock()
    result.all = lambda: [(uid, f"h{uid}") for uid in targets]
    db.execute = AsyncMock(return_value=result)
    service_bulk = PermissionService(db)
    service_bulk._redis = redis_bulk

    with patch(
        "app.services.ai.skills.policy.clear_forbidden_skill_cache", lambda: None
    ):
        await service_bulk.invalidate_cached_permissions_for_users(targets)

    single_keys = set(redis_single.deleted)
    bulk_keys = set(redis_bulk.deleted)

    missing = single_keys - bulk_keys
    extra = bulk_keys - single_keys
    assert not missing, f"批量路径漏删了单用户路径会删的键：{sorted(missing)}"
    assert not extra, f"批量路径多删了单用户路径不删的键：{sorted(extra)}"

    # 两条路径删完后，残留的键也必须一致（证明没有「漏网」的键族）
    assert redis_single.store == redis_bulk.store
