import pytest
import asyncio
import logging
import os
import time
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core import database, redis
from sqlalchemy import delete, select
from app.models.user import User
from app.core.orm import AsyncSessionLocal
from app.services.audit_service import AuditService
from app.utils.encryption import get_api_key_manager
from unittest.mock import AsyncMock, patch

# event_loop fixture removed to let pytest-asyncio handle it automatically

logger = logging.getLogger(__name__)


def _should_flush_redis_for_tests() -> bool:
    """仅当显式开启时才 FLUSHDB，避免误清本机/共享 Redis 里的记忆索引等数据。

    CI 或隔离测试库可设：TEST_REDIS_FLUSH=1
    """
    return str(os.environ.get("TEST_REDIS_FLUSH", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure(request):
    """Initialize DB and Redis for each function."""
    if request.node.get_closest_marker("no_infrastructure"):
        yield
        return

    # Force reset engine pool to bind to current loop
    await database.close_db() 
    
    await database.init_db()
    await redis.init_redis()
    
    # 默认不再 FLUSHDB：开发者本机 Redis 常与业务共用，flush 会删掉记忆索引等。
    # 需要隔离环境时可 export TEST_REDIS_FLUSH=1
    r = await redis.get_redis()
    if r:
        if _should_flush_redis_for_tests():
            await r.flushdb()
        else:
            logger.warning(
                "Skip Redis FLUSHDB (set TEST_REDIS_FLUSH=1 to enable). "
                "Shared Redis data such as memory indexes will be preserved."
            )

    # Seed a default model for tests that need LLM
    from app.models.ai_model import AIModel
    from app.services.config_service import ConfigService
    async with AsyncSessionLocal() as session:
        # 1. Seed LLM Model
        from sqlalchemy import select
        res = await session.execute(select(AIModel).where(AIModel.model_id == "deepseek-chat"))
        if not res.scalar_one_or_none():
            session.add(AIModel(
                id="test-model-uuid",
                name="DeepSeek Default",
                model_id="deepseek-chat",
                provider="openai",
                type="llm",
                api_key="sk-test-key",
                api_base_url="https://api.deepseek.com/v1",
                is_active=True
            ))
            await session.commit()
            
    yield
    await database.close_db()
    await redis.close_redis()

@pytest.fixture(autouse=True)
async def mock_audit_manager():
    """Global mock for AuditManager."""
    async def noop_coro(*args, **kwargs):
        pass

    with patch("app.services.ai.agent_service.AuditManager") as mock_audit:
        mock_audit.save_trace_logs = AsyncMock(side_effect=noop_coro)
        mock_audit.save_history = AsyncMock(side_effect=noop_coro)
        mock_audit.log_transaction = AsyncMock(side_effect=noop_coro)
        yield mock_audit

@pytest.fixture
async def seed_data():
    """Seed test users with idempotency. Concurrent-safe for shared DB environments."""
    manager = get_api_key_manager()
    admin_key = "TestAdmin_4wMogHLKDhTDmdwaYFs2ubNDVLXq6Fp4egn0uQ"
    user_key = "TestUser_yf4wflfNQiggz3HD2Px5o2dJEVl6rcgLoiDJa8I"
    
    admin_hash = manager.hash_api_key(admin_key)
    user_hash = manager.hash_api_key(user_key)
    admin_encrypted = manager.encrypt_api_key(admin_key)
    user_encrypted = manager.encrypt_api_key(user_key)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        
        for uname, urole, uhash, uenc in [
            ("test_admin", "admin", admin_hash, admin_encrypted),
            ("test_user", "user", user_hash, user_encrypted)
        ]:
            try:
                # 1. Try to find
                res = await session.execute(select(User).where(User.user_name == uname))
                existing = res.scalar_one_or_none()
                
                if existing:
                    # 2. Update existing to ensure correct state
                    existing.api_key_hash = uhash
                    existing.api_key_encrypted = uenc
                    existing.role = urole
                    existing.status = 1
                else:
                    # 3. Try to insert
                    session.add(User(
                        user_name=uname,
                        api_key_hash=uhash,
                        api_key_encrypted=uenc,
                        role=urole,
                        status=1
                    ))
                await session.commit()
            except IntegrityError:
                # Concurrent insert happened, just rollback and continue
                await session.rollback()
                # Re-verify/Update
                async with AsyncSessionLocal() as retry_session:
                    res = await retry_session.execute(select(User).where(User.user_name == uname))
                    existing = res.scalar_one_or_none()
                    if existing:
                        existing.api_key_hash = uhash
                        existing.api_key_encrypted = uenc
                        existing.role = urole
                        existing.status = 1
                        await retry_session.commit()
            except Exception as e:
                await session.rollback()
                print(f"Warning: Unexpected error seeding {uname}: {e}")
        
    yield
    # No cleanup here to prevent interfering with other concurrent tests in shared DB environment.
    # The data is small and static (test_admin/test_user), so leaving it is safer than deleting it while others use it.

@pytest.fixture
async def db_session():
    """Returns an async session."""
    async with AsyncSessionLocal() as session:
        yield session
        # No commit here, let individual tests handle commit/rollback
        # Or rollback to keep clean state
        # await session.rollback() # Optional
        await session.close()

@pytest.fixture
async def client(seed_data) -> AsyncClient:
    """Async client for testing."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac

@pytest.fixture
def valid_api_key() -> str:
    return "TestUser_yf4wflfNQiggz3HD2Px5o2dJEVl6rcgLoiDJa8I"

@pytest.fixture
def admin_api_key() -> str:
    return "TestAdmin_4wMogHLKDhTDmdwaYFs2ubNDVLXq6Fp4egn0uQ"


# --------------------------------------------------------------------------- #
# 注册开关与测试账号清理
#
# 原先定义在 tests/test_user_registration.py 模块内。加邮箱字段时出现了第二个
# 需要它们的测试文件，而 tests/ 不是 package（没有 __init__.py），测试模块之间
# 无法 import 彼此的辅助代码——所以上移到 conftest，由 pytest 自动发现。
# --------------------------------------------------------------------------- #

async def clear_register_limits() -> None:
    """清空注册相关的限流计数（账号名、来源地址与预检三套键共用同一前缀）。"""
    redis_client = await redis.get_redis()
    if not redis_client:
        return
    keys = []
    async for key in redis_client.scan_iter(match="auth:register:*"):
        keys.append(key)
    if keys:
        await redis_client.delete(*keys)


async def _toggle_registration(value: str) -> None:
    from app.services.config_service import (
        USER_REGISTRATION_ENABLED_KEY,
        ConfigService,
    )
    await ConfigService.set_config(
        USER_REGISTRATION_ENABLED_KEY, value, category="general", changed_by="pytest"
    )


async def _current_registration() -> str:
    from app.services.config_service import (
        USER_REGISTRATION_ENABLED_KEY,
        ConfigService,
    )
    return await ConfigService.get(USER_REGISTRATION_ENABLED_KEY) or "false"


@pytest.fixture
async def registration_on():
    """开启注册开关，并在用例结束后还原、清理限流计数。"""
    # 必须在改之前捕获原值：teardown 时再读拿到的已经是被改过的值，
    # 那样「还原」会变成永远写回 false，污染后续用例。
    previous = await _current_registration()
    await _toggle_registration("true")
    await clear_register_limits()
    yield
    await _toggle_registration(previous)
    await clear_register_limits()


@pytest.fixture
async def registration_off():
    """显式关闭注册开关，并在用例结束后还原。"""
    previous = await _current_registration()
    await _toggle_registration("false")
    await clear_register_limits()
    yield
    await _toggle_registration(previous)
    await clear_register_limits()


@pytest.fixture
async def cleanup_registered():
    """收集并清理本用例创建的账号（含角色关联）。用例把账号名 append 进来即可。"""
    from app.models.permission import UserRoleRelation

    names: list[str] = []
    yield names
    if not names:
        return
    async with AsyncSessionLocal() as session:
        ids = (
            await session.execute(select(User.id).where(User.user_name.in_(names)))
        ).scalars().all()
        if ids:
            await session.execute(
                delete(UserRoleRelation).where(UserRoleRelation.user_id.in_(ids))
            )
        await session.execute(delete(User).where(User.user_name.in_(names)))
        await session.commit()


@pytest.fixture
def wait_for_access_log():
    """等待审计日志落库，返回该 trace_id 的记录（超时返回 None）。

    审计日志由中间件挂在响应的 `BackgroundTask` 上，**在响应返回之后**才入队；
    而 `AuditService.flush()` 只排空进程内队列。若 background 尚未执行，flush 就是
    空转，随后立刻查库会读到"还没写入"——这正是既有测试随机失败的原因（非功能缺陷）。

    这里改为轮询等待；超时仍返回 None，因此调用方的 `assert row is not None`
    强度不变，真正写不进去时依旧会失败。
    """
    async def _wait(trace_id: str, timeout: float = 5.0):
        from sqlalchemy import text

        deadline = time.monotonic() + timeout
        while True:
            await AuditService.flush()
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text(
                        "SELECT request_params, user_name, status_code "
                        "FROM ai_agent_access_logs WHERE trace_id = :trace_id"
                    ),
                    {"trace_id": trace_id},
                )
                row = result.fetchone()
            if row is not None:
                return {
                    "request_params": row[0],
                    "user_name": row[1],
                    "status_code": row[2],
                }
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.05)

    return _wait


# ---------------------------------------------------------------------------
# 已知失败基线（known failures baseline）
# ---------------------------------------------------------------------------
# 目的：把"长期存在的失败"与"本次改动引入的回归"区分开。
#
# 命中 tests/known_failures.txt 的用例默认被标记为 xfail —— 全仓因此保持绿色，
# 而任何**不在**清单中的失败都会照常报红，那才是回归信号。
#
#   pytest tests/                          默认：清单内 xfail，清单外报红
#   pytest tests/ --run-known-failures     照常运行清单内用例（看真实修没修好）
#   pytest tests/ --strict-known-failures  清单内用例已修好则报错（督促清理清单）
#
# 清单格式与维护规则见 tests/known_failures.txt 顶部注释。

import importlib.util as _importlib_util
import warnings as _warnings
from pathlib import Path as _Path

_KF_PATH = _Path(__file__).parent / "known_failures.py"
_kf_spec = _importlib_util.spec_from_file_location("_tests_known_failures", _KF_PATH)
known_failures = _importlib_util.module_from_spec(_kf_spec)
_kf_spec.loader.exec_module(known_failures)


def pytest_addoption(parser):
    group = parser.getgroup("known-failures", "已知失败基线")
    group.addoption(
        "--run-known-failures",
        action="store_true",
        default=False,
        help="照常运行已知失败用例（默认它们被标记为 xfail，不阻塞运行）",
    )
    group.addoption(
        "--strict-known-failures",
        action="store_true",
        default=False,
        help="要求清单中的用例仍然失败；已修复却未从清单移除时让运行失败",
    )


def pytest_collection_modifyitems(config, items):
    known = known_failures.load_known_failures()
    if not known:
        return

    gone = known_failures.missing_from_collection(
        known, {item.nodeid for item in items}
    )
    if gone:
        preview = "、".join(gone[:5]) + (" ..." if len(gone) > 5 else "")
        _warnings.warn(
            f"known_failures.txt 中有 {len(gone)} 条找不到对应用例"
            f"（拼写错误，或测试已改名/删除）：{preview}",
            pytest.PytestConfigWarning,
        )

    if config.getoption("--run-known-failures"):
        return

    known_failures.mark_known_failures(
        items, known, strict=config.getoption("--strict-known-failures")
    )
