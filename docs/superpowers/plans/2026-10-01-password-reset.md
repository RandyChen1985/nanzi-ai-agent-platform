# 忘记密码（邮件找回）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让未登录用户能用邮箱收到一条 30 分钟有效、只能使用一次的重置链接，重设密码并强制下线所有会话；同时保证「邮箱是否存在」无法被枚举。

**Architecture:** 新增 `PasswordResetService` 作为唯一入口，把「token 签发/核销 + 三层风控 + 重置编排」收在一处，使「校验先于核销」的顺序不可能被端点写错。token 存 Redis（`auth:pwdreset:{uid}` → nonce 的 sha256，TTL 1800），签发即覆盖实现「新申请作废旧链接」，`GETDEL` 实现单次使用。两个新端点挂在唯一免鉴权的 `auth` 路由组下；发信通过 `BackgroundTasks` 在响应之后进行，以消除 SMTP 耗时造成的枚举预言机。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / Pydantic 2 / redis-py 8（`getdel`）/ pytest / Vue 3 + TS + Vite

**依据规范：** `docs/superpowers/specs/2026-10-01-password-reset-design.md`（本计划的每个任务都对应它的一节）

**仓库约定（务必遵守）：**

- **零数据库迁移**：不新增 `db-prod/*.sql` 与 `db-prod-pg/*.sql`
- **不自行 `git commit`**：本仓库要求提交由用户明确指令触发。因此每个任务以「运行验证」收尾而非「提交」；全部完成后统一询问用户是否提交
- 后端测试用 `.venv/bin/python -m pytest`（裸 `python` 不存在）；前端契约测试用 `--confcutdir=tests/frontend`
- 本仓库约定中文注释与中文提交信息

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/services/password_reset_service.py` | 创建 | token 签发/核销、三层风控、重置编排、邮件正文构造。**本特性唯一的业务逻辑所在地** |
| `app/api/portal/endpoints/auth.py` | 修改 | 两个端点 + 两个 Pydantic 模型 + `/config/public` 新字段 + `PUT /password` 吊销会话 |
| `app/services/auth_service.py` | 修改 | 新增 `reset_email_limits()` 限流常量与键函数（与既有注册限流并列） |
| `frontend/src/views/ResetPassword.vue` | 创建 | 重置页（邮箱模式 / 新密码模式） |
| `frontend/src/router/index.ts` | 修改 | 新增公开路由 `/reset-password` |
| `frontend/src/views/Login.vue` | 修改 | 「忘记密码？」入口（受 `password_reset_available` 控制） |
| `frontend/src/views/PersonalCenter.vue` | 修改 | 改密成功后清凭据并跳登录页（会话已被服务端吊销） |
| `frontend/src/views/SystemConfig.vue` | 修改 | `download_url_prefix` 短说明 + 💡 示例 + 搜索扩展 |
| `frontend/src/components/system/DeploymentChecklist.vue` | 修改 | 该配置项说明补「重置链接」 |
| `tests/test_password_reset.py` | 创建 | 后端行为测试（25 项中的 19 项） |
| `tests/frontend/test_password_reset_contract.py` | 创建 | 前端契约测试（6 项） |
| `tests/CHECKLIST.md` | 修改 | 交付记录 |

---

## Task 0: 测试夹具（后续任务都依赖，必须先写）

**Files:**
- Create: `tests/test_password_reset.py`

复用 `tests/test_user_email_and_mail_service.py` 里已验证过的造数写法：`AuthService.generate_api_key(...)`
**自身落库并只返回明文 API Key**，`_drop_user` 负责清理。不要另发明一套造数方式。

- [ ] **Step 1: 写夹具**

```python
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.auth_service import AuthService

USER_STATUS_ENABLED = 1
USER_STATUS_DISABLED = 0
USER_STATUS_PENDING = 2


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _seed_with_status(user_name: str, email: str, status: int):
    """建一个指定状态的用户，返回 (uid, email)。

    注意 generate_api_key 的返回语义：它内部生成 key/encrypted/hash 并自行落库，
    只把**明文 key** 返回给调用方；这里不需要 key，所以忽略返回值。
    """
    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="找回密码测试", status=status, email=email, db=s
        )
        uid = (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar_one()
        return uid, email


async def _drop_user(user_name: str) -> None:
    async with AsyncSessionLocal() as s:
        uid = (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar()
        if uid is not None:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


@pytest.fixture
async def enabled_account():
    name = _name("zreset_en")
    uid, email = await _seed_with_status(
        name, f"{name}@corp.example.com", USER_STATUS_ENABLED
    )
    try:
        yield uid, email
    finally:
        await _drop_user(name)


@pytest.fixture
async def disabled_account():
    name = _name("zreset_dis")
    uid, email = await _seed_with_status(
        name, f"{name}@corp.example.com", USER_STATUS_DISABLED
    )
    try:
        yield uid, email
    finally:
        await _drop_user(name)


@pytest.fixture
async def pending_account():
    name = _name("zreset_pend")
    uid, email = await _seed_with_status(
        name, f"{name}@corp.example.com", USER_STATUS_PENDING
    )
    try:
        yield uid, email
    finally:
        await _drop_user(name)
```

- [ ] **Step 2: 确认可收集**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q`
Expected: `no tests ran`（此刻还没有用例，属正常）

---

## Task 1: 限流常量与键函数（`AuthService`）

**Files:**
- Modify: `app/services/auth_service.py`（紧跟既有注册限流常量之后，约 `REGISTER_CHECK_PREFIX` 之后）
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_password_reset.py
"""忘记密码（邮件找回）行为测试。

守卫重点：防枚举一致性、token 一次性、新申请作废旧链接、校验先于核销。
"""
from app.services.auth_service import AuthService


def test_password_reset_limit_constants_and_key_shapes():
    assert AuthService.PWD_RESET_TTL_SECONDS == 1800
    assert AuthService.PWD_RESET_COOLDOWN_SECONDS == 60
    assert AuthService.PWD_RESET_EMAIL_LIMIT == 3
    assert AuthService.PWD_RESET_SOURCE_LIMIT == 5
    # 键名不得出现明文邮箱（监控/慢日志/KEYS 都会打印键名）
    key = AuthService.pwd_reset_email_count_key("Victim@Example.com")
    assert "victim" not in key.lower()
    assert "example.com" not in key.lower()
    assert key.startswith("auth:pwdreset:cnt:email:")
    # 同一个邮箱不同大小写必须落到同一个桶
    assert key == AuthService.pwd_reset_email_count_key("victim@example.com")
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py::test_password_reset_limit_constants_and_key_shapes -q`
Expected: FAIL — `AttributeError: type object 'AuthService' has no attribute 'PWD_RESET_TTL_SECONDS'`

- [ ] **Step 3: 实现**

```python
    # --- 忘记密码（邮件找回）限流 ---
    # 与注册/登录限流同策略：Redis 不可用时 fail-open。三个维度：
    #   ① 同邮箱 60 秒冷却 —— 挡住连点，也让「刚被申请过」无法被用来探测；
    #   ② 同邮箱 3 次/小时 —— 挡住对单个邮箱的邮件轰炸；
    #   ③ 同来源 5 次/小时 —— 挡住换邮箱刷量（来源可信度判定复用注册那套）。
    #
    # 键名一律用 sha256(小写邮箱) 而不是明文：Redis 键名会出现在监控、慢日志与 KEYS
    # 输出里，用户邮箱不该被写进去。
    PWD_RESET_TTL_SECONDS = 1800           # 链接有效期 30 分钟
    PWD_RESET_COOLDOWN_SECONDS = 60        # 同邮箱冷却
    PWD_RESET_EMAIL_LIMIT = 3              # 同邮箱每小时
    PWD_RESET_SOURCE_LIMIT = 5             # 同来源每小时
    PWD_RESET_WINDOW_SECONDS = 3600
    PWD_RESET_PREFIX = "auth:pwdreset:"

    @staticmethod
    def pwd_reset_email_fingerprint(email: str) -> str:
        import hashlib

        text = (email or "").strip().lower().encode("utf-8")
        return hashlib.sha256(text).hexdigest()

    @staticmethod
    def pwd_reset_cooldown_key(email: str) -> str:
        return f"{AuthService.PWD_RESET_PREFIX}cd:email:{AuthService.pwd_reset_email_fingerprint(email)}"

    @staticmethod
    def pwd_reset_email_count_key(email: str) -> str:
        return f"{AuthService.PWD_RESET_PREFIX}cnt:email:{AuthService.pwd_reset_email_fingerprint(email)}"

    @staticmethod
    def pwd_reset_source_count_key(source: str) -> str:
        return f"{AuthService.PWD_RESET_PREFIX}cnt:ip:{(source or 'unknown').strip()}"
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py::test_password_reset_limit_constants_and_key_shapes -q`
Expected: PASS

---

## Task 2: `PasswordResetService` —— token 签发、校验、原子核销

**Files:**
- Create: `app/services/password_reset_service.py`
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest

from app.services.password_reset_service import PasswordResetService


class FakeRedis:
    """最小 Redis 替身：只需要 get/set/del/getdel/incr/expire/ttl。"""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.getdel_calls = 0

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = value
        if ex is not None:
            self.ttls[key] = int(ex)
        return True

    async def get(self, key):
        return self.data.get(key)

    async def getdel(self, key):
        self.getdel_calls += 1
        return self.data.pop(key, None)

    async def delete(self, *keys):
        return sum(1 for k in keys if self.data.pop(k, None) is not None)

    async def incr(self, key):
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    async def expire(self, key, seconds):
        self.ttls[key] = int(seconds)
        return True

    async def ttl(self, key):
        return self.ttls.get(key, -1)


@pytest.mark.no_infrastructure
async def test_issue_token_stores_hash_not_plaintext_and_sets_ttl():
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    uid, _, nonce = token.partition(".")
    assert uid == "7" and len(nonce) > 20
    stored = redis.data["auth:pwdreset:7"]
    assert stored != nonce, "Redis 里必须是哈希，不能是明文 nonce"
    assert stored == PasswordResetService.hash_nonce(nonce)
    assert redis.ttls["auth:pwdreset:7"] == 1800


@pytest.mark.no_infrastructure
async def test_new_issue_overwrites_previous_token():
    redis = FakeRedis()
    first = await PasswordResetService.issue_token(7, redis)
    second = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.consume_token(first, redis) is None, "旧链接必须失效"
    assert await PasswordResetService.consume_token(second, redis) == 7


@pytest.mark.no_infrastructure
async def test_consume_token_is_single_use():
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.consume_token(token, redis) == 7
    assert await PasswordResetService.consume_token(token, redis) is None, "第二次必须失败"


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: "8." + t.partition(".")[2],          # 改 uid
        lambda t: t.partition(".")[0] + ".deadbeef",   # 改 nonce
        lambda t: t.replace(".", "", 1),               # 去掉分隔符
        lambda t: "",                                  # 空
        lambda t: "abc.def",                           # uid 非数字
    ],
)
async def test_consume_token_rejects_tampered_tokens(mutate):
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.consume_token(mutate(token), redis) is None


@pytest.mark.no_infrastructure
async def test_verify_token_does_not_burn_it():
    """只读校验不得核销 —— 这是「密码不合规不该吃掉链接」的前提。"""
    redis = FakeRedis()
    token = await PasswordResetService.issue_token(7, redis)

    assert await PasswordResetService.verify_token(token, redis) == 7
    assert redis.getdel_calls == 0
    assert await PasswordResetService.consume_token(token, redis) == 7
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "token"`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.password_reset_service'`

- [ ] **Step 3: 实现**

```python
"""忘记密码（邮件找回）服务。

三条不可动摇的约束（都来自设计文档，且都由本模块保证，端点无从写错）：

1. **校验先于核销**：`verify_token` 只读，`consume_token` 才核销。任何校验
   （密码复杂度、账号状态）都排在核销之前，否则用户只是密码设得不够强，就白白
   失去这条链接，必须重走一遍邮件流程。这与 `embed_service` 里「来源校验失败却把
   票烧掉」的踩坑是同构问题。
2. **一次性**：核销用 `GETDEL`（原子取删），并发两个请求只有一个能拿到值。
3. **新申请作废旧链接**：每个用户只有一个键，签发即覆盖写，因此不存在
   「两条链接同时有效」的可能，也不需要读旧值（没有并发窗口）。

为什么把编排放在服务层而不是端点里：顺序性约束一旦暴露给调用方，迟早会被写错。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# 防枚举的对外文案。所有「不该发信」的分支都必须返回这一句，
# 包括被限流的分支 —— 否则攻击者能用「操作过于频繁」反推某个邮箱刚被申请过。
UNIFIED_MESSAGE = "如果该邮箱已注册，我们已发送重置邮件"
INVALID_TOKEN_MESSAGE = "链接无效或已过期，请重新申请"
RESET_SUCCESS_MESSAGE = "密码重置成功，请使用新密码登录"


def _token_key(user_id: int) -> str:
    return f"{AuthService.PWD_RESET_PREFIX}{int(user_id)}"


class PasswordResetService:
    # ---------------- token ---------------- #

    @staticmethod
    def hash_nonce(nonce: str) -> str:
        return hashlib.sha256((nonce or "").encode("utf-8")).hexdigest()

    @staticmethod
    def parse_token(token: str) -> Optional[Tuple[int, str]]:
        """解析 `{uid}.{nonce}`；任何形态异常一律返回 None。"""
        if not token or not isinstance(token, str):
            return None
        uid_text, sep, nonce = token.partition(".")
        if not sep or not uid_text or not nonce:
            return None
        if not uid_text.isdigit():
            return None
        return int(uid_text), nonce

    @staticmethod
    async def issue_token(user_id: int, redis) -> str:
        """签发并**覆盖**该用户的旧链接。返回明文 token（只出现在邮件里）。"""
        nonce = secrets.token_urlsafe(32)
        await redis.set(
            _token_key(user_id),
            PasswordResetService.hash_nonce(nonce),
            ex=AuthService.PWD_RESET_TTL_SECONDS,
        )
        return f"{int(user_id)}.{nonce}"

    @staticmethod
    async def _read_stored(redis, key: str) -> Optional[str]:
        raw = await redis.get(key)
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    @staticmethod
    async def verify_token(token: str, redis) -> Optional[int]:
        """**只读**校验，返回 user_id；不产生任何副作用。"""
        parsed = PasswordResetService.parse_token(token)
        if not parsed:
            return None
        user_id, nonce = parsed
        stored = await PasswordResetService._read_stored(redis, _token_key(user_id))
        if not stored:
            return None
        if not hmac.compare_digest(PasswordResetService.hash_nonce(nonce), stored):
            return None
        return user_id

    @staticmethod
    async def consume_token(token: str, redis) -> Optional[int]:
        """校验并原子核销（GETDEL），返回 user_id；任何失败都不消耗 token。

        先 GET 校验、再 GETDEL 核销、最后比对「核销到的」与「刚校验的」是否一致：
        直接 GETDEL 会在校验失败时把 token 烧掉；而只比一次又会给
        「同一 key 被换进另一张票」留下窗口。范式与 embed_service 一致。
        """
        parsed = PasswordResetService.parse_token(token)
        if not parsed:
            return None
        user_id, nonce = parsed
        key = _token_key(user_id)
        stored = await PasswordResetService._read_stored(redis, key)
        if not stored:
            return None
        if not hmac.compare_digest(PasswordResetService.hash_nonce(nonce), stored):
            return None

        consumed = await redis.getdel(key)
        if consumed is None:
            return None
        consumed_text = consumed.decode("utf-8") if isinstance(consumed, bytes) else str(consumed)
        if consumed_text != stored:
            return None
        return user_id
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "token"`
Expected: PASS（7 项）

---

## Task 3: 三层风控

**Files:**
- Modify: `app/services/password_reset_service.py`
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.no_infrastructure
async def test_rate_limit_cooldown_blocks_second_request():
    redis = FakeRedis()
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is True
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is False
    # 冷却键的 TTL 必须是 60 秒
    assert redis.ttls[AuthService.pwd_reset_cooldown_key("a@x.com")] == 60


@pytest.mark.no_infrastructure
async def test_rate_limit_email_hourly_quota():
    redis = FakeRedis()
    # 人为跳过冷却：直接构造已用满的计数
    redis.data[AuthService.pwd_reset_email_count_key("a@x.com")] = "3"
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is False


@pytest.mark.no_infrastructure
async def test_rate_limit_source_hourly_quota():
    redis = FakeRedis()
    redis.data[AuthService.pwd_reset_source_count_key("1.2.3.4")] = "5"
    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is False


@pytest.mark.no_infrastructure
async def test_rate_limit_counts_both_dimensions():
    redis = FakeRedis()
    for i in range(2):
        redis.data.pop(AuthService.pwd_reset_cooldown_key("a@x.com"), None)
        assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", redis) is True
    assert redis.data[AuthService.pwd_reset_email_count_key("a@x.com")] == "2"
    assert redis.data[AuthService.pwd_reset_source_count_key("1.2.3.4")] == "2"
    # 计数窗口是 1 小时
    assert redis.ttls[AuthService.pwd_reset_email_count_key("a@x.com")] == 3600


@pytest.mark.no_infrastructure
async def test_rate_limit_fails_open_on_redis_error():
    """Redis 单条命令异常要 fail-open（与注册/登录限流一致），不能把功能整体关掉。"""

    class Broken(FakeRedis):
        async def get(self, key):
            raise RuntimeError("redis boom")

    assert await PasswordResetService.pass_rate_limits("a@x.com", "1.2.3.4", Broken()) is True
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "rate_limit"`
Expected: FAIL — `AttributeError: ... has no attribute 'pass_rate_limits'`

- [ ] **Step 3: 实现**

```python
    # ---------------- 风控 ---------------- #

    @staticmethod
    async def _safe(fn, default):
        """Redis 单条命令异常的降级包装：告警后返回 default。

        与注册/登录限流同策略：限流组件故障不能把功能整体关掉（fail-open）。
        Redis **整体**不可用是另一回事，由端点在第 3 步直接 503。
        """
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("密码重置限流读写失败，本次跳过：%s", exc)
            return default

    @staticmethod
    async def _count(redis, key: str) -> int:
        async def _read():
            value = await redis.get(key)
            if value is None:
                return 0
            text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            return int(text)

        return await PasswordResetService._safe(_read, 0)

    @staticmethod
    async def pass_rate_limits(email: str, source: str, redis) -> bool:
        """三层风控：邮箱小时配额 → 来源小时配额 → 邮箱冷却。

        通过则占用冷却并累加两个计数，返回 True；任一维度触顶返回 False。
        冷却**在最后**才占用：被前面两档拒绝的请求不应该顺带占掉冷却窗口。
        """
        email_count_key = AuthService.pwd_reset_email_count_key(email)
        source_count_key = AuthService.pwd_reset_source_count_key(source)
        cooldown_key = AuthService.pwd_reset_cooldown_key(email)

        if await PasswordResetService._count(redis, email_count_key) >= AuthService.PWD_RESET_EMAIL_LIMIT:
            return False
        if await PasswordResetService._count(redis, source_count_key) >= AuthService.PWD_RESET_SOURCE_LIMIT:
            return False

        async def _acquire_cooldown():
            # SET NX EX 是原子的：并发请求只有一个能拿到
            return await redis.set(
                cooldown_key, "1", ex=AuthService.PWD_RESET_COOLDOWN_SECONDS, nx=True
            )

        acquired = await PasswordResetService._safe(_acquire_cooldown, True)
        if not acquired:
            return False

        async def _bump(key: str) -> None:
            count = int(await redis.incr(key))
            if count == 1:
                await redis.expire(key, AuthService.PWD_RESET_WINDOW_SECONDS)
            else:
                ttl = await redis.ttl(key)
                if ttl is not None and int(ttl) < 0:
                    await redis.expire(key, AuthService.PWD_RESET_WINDOW_SECONDS)

        await PasswordResetService._safe(lambda: _bump(email_count_key), None)
        await PasswordResetService._safe(lambda: _bump(source_count_key), None)
        return True
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "rate_limit"`
Expected: PASS（5 项）

---

## Task 4: 请求侧编排 + 邮件正文

**Files:**
- Modify: `app/services/password_reset_service.py`
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.no_infrastructure
async def test_find_deliverable_only_for_enabled(
    db_session, enabled_account, disabled_account, pending_account
):
    """只有启用账号才会真发信；待审核与已禁用一律 None（静默，防枚举）。"""
    redis = FakeRedis()
    _, enabled_email = enabled_account
    _, disabled_email = disabled_account
    _, pending_email = pending_account

    hit = await PasswordResetService.request_reset(db_session, enabled_email, "1.2.3.4", redis)
    assert hit is not None and hit[1] == enabled_email

    # 换来源地址，避免被同一来源的配额挡住而误判
    assert await PasswordResetService.request_reset(
        db_session, disabled_email, "2.2.2.2", redis
    ) is None
    assert await PasswordResetService.request_reset(
        db_session, pending_email, "3.3.3.3", redis
    ) is None
    assert await PasswordResetService.request_reset(
        db_session, "nobody@nowhere.example.com", "4.4.4.4", redis
    ) is None

```python
@pytest.mark.no_infrastructure
async def test_build_link_uses_download_url_prefix(monkeypatch):
    async def _prefix():
        return "https://portal.example.com"

    monkeypatch.setattr(
        "app.services.password_reset_service.get_download_url_prefix", _prefix
    )
    link = await PasswordResetService.build_link(7, FakeRedis())
    assert link.startswith("https://portal.example.com/reset-password?token=7.")


@pytest.mark.no_infrastructure
async def test_build_link_returns_none_when_prefix_empty(monkeypatch):
    async def _prefix():
        return ""

    monkeypatch.setattr(
        "app.services.password_reset_service.get_download_url_prefix", _prefix
    )
    # 空前缀会拼出邮件里点不开的相对地址，因此宁可返回 None（端点据此 503）
    assert await PasswordResetService.build_link(7, FakeRedis()) is None


@pytest.mark.no_infrastructure
def test_reset_mail_body_mentions_ttl_and_one_time():
    subject, body = PasswordResetService.build_mail("https://x/reset-password?token=7.abc")
    assert "密码重置" in subject
    assert "https://x/reset-password?token=7.abc" in body
    assert "30 分钟" in body
    assert "一次" in body
    assert "忽略" in body
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "build_link or reset_mail_body"`
Expected: FAIL — 方法不存在

- [ ] **Step 3: 实现**

```python
from app.services.ai.tools.generated_file_service import get_download_url_prefix

RESET_PAGE_PATH = "/reset-password"


    @staticmethod
    async def availability() -> Optional[str]:
        """返回 None 表示功能可用；否则返回**给管理员看的**不可用原因。

        端点、`/config/public` 都只调这一个函数，保证「入口是否显示」与
        「接口是否可调」用的是同一套判定，不会出现「入口显示但接口 503」。
        测试里也只需要 patch 一处（本模块名下的 get_download_url_prefix 与邮件服务）。
        """
        from app.services.email_delivery_service import EmailDeliveryService

        if await EmailDeliveryService.load_global_smtp_settings() is None:
            return "邮件找回功能未开启，请联系管理员"
        if not (await get_download_url_prefix()).strip():
            return "平台未配置对外访问地址，请先在系统配置中填写 download_url_prefix"
        return None

    @staticmethod
    async def build_link(user_id: int, redis) -> Optional[str]:
        """签发 token 并拼出完整重置链接。

        前缀为空时必须返回 None 而不是拼相对地址：下载链接退化成相对地址仍可用
        （用户就在 Web 应用里点），但**邮件里的相对地址点不开**，发出去就是废邮件。
        """
        prefix = (await get_download_url_prefix()).strip()
        if not prefix:
            return None
        token = await PasswordResetService.issue_token(user_id, redis)
        return f"{prefix}{RESET_PAGE_PATH}?token={token}"

    @staticmethod
    def build_mail(link: str) -> Tuple[str, str]:
        subject = "AI 智能体平台 - 密码重置"
        body = (
            "我们收到了重置你账号密码的请求。\n\n"
            f"请在 30 分钟内点击下面的链接设置新密码（该链接只能使用一次）：\n\n"
            f"{link}\n\n"
            "如果链接已过期，请在登录页重新发起找回密码。\n\n"
            "如果这不是你本人的操作，请忽略本邮件，你的密码不会被修改。\n"
            "如果你怀疑账号已被他人使用，建议登录后到「个人中心」重置 API Key。\n"
        )
        return subject, body
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "build_link or reset_mail_body"`
Expected: PASS（3 项）

---

## Task 5: 重置编排（`reset_password`）—— 顺序性约束的唯一落点

**Files:**
- Modify: `app/services/password_reset_service.py`
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.no_infrastructure
async def test_reset_password_rejects_weak_password_without_burning_token(
    db_session, enabled_account
):
    """密码不合规 → 返回具体原因，且 token 仍然可用（这条守住「校验先于核销」）。"""
    redis = FakeRedis()
    uid, _ = enabled_account
    token = await PasswordResetService.issue_token(uid, redis)

    ok, message = await PasswordResetService.reset_password(
        token, "short", db_session, redis
    )
    assert ok is False
    assert message != INVALID_TOKEN_MESSAGE, "密码问题是可修复错误，应给出具体原因"
    assert await PasswordResetService.verify_token(token, redis) == uid, "链接不能被烧掉"

    ok, message = await PasswordResetService.reset_password(
        token, "Str0ng#Passw0rd", db_session, redis
    )
    assert ok is True


@pytest.mark.no_infrastructure
async def test_reset_password_invalid_token_message_is_unified(db_session):
    redis = FakeRedis()
    for bad in ("", "abc", "999.deadbeef", "1.not-the-nonce"):
        ok, message = await PasswordResetService.reset_password(
            bad, "Str0ng#Passw0rd", db_session, redis
        )
        assert ok is False
        assert message == INVALID_TOKEN_MESSAGE
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "reset_password"`
Expected: FAIL — `reset_password` 不存在

- [ ] **Step 3: 实现**

```python
    # ---------------- 重置编排 ---------------- #

    @staticmethod
    async def reset_password(
        token: str, new_password: str, db: AsyncSession, redis
    ) -> Tuple[bool, str]:
        """校验 → 核销 → 改密 → 吊销会话。返回 (是否成功, 可直接展示的文案)。

        **所有校验都在核销之前**（见模块头注释约束 1）：
          1 校验 token（只读）→ 2 查账号与状态 → 3 密码复杂度 → 4 原子核销
          → 5 改密 → 6 吊销该用户全部会话。
        """
        user_id = await PasswordResetService.verify_token(token, redis)
        if user_id is None:
            return False, INVALID_TOKEN_MESSAGE

        row = (
            await db.execute(
                select(User.id, User.user_name, User.status).where(User.id == user_id)
            )
        ).first()
        if row is None or int(row.status) != 1:
            # 账号不存在/待审核/已禁用一律同一句，不把状态区分出来
            return False, INVALID_TOKEN_MESSAGE

        valid, reason = AuthService.validate_password_complexity(
            new_password, username=row.user_name
        )
        if not valid:
            # 此时**尚未核销**，链接仍然可用
            return False, reason

        consumed_user_id = await PasswordResetService.consume_token(token, redis)
        if consumed_user_id is None:
            # 并发下已被别人用掉，或同一 key 被换成了另一张票
            return False, INVALID_TOKEN_MESSAGE

        password = new_password
        raw = password.encode("utf-8")
        if len(raw) > 72:                      # bcrypt 上限，与 PUT /auth/password 一致
            password = raw[:72].decode("utf-8", errors="ignore")

        await AuthService.set_user_password(user_id, password, db=db)
        await AuthService.revoke_sessions_for_user(user_id)

        # 审计：与「自助改邮箱」同口径（结构化日志，不建审计表）；绝不记录 token 与密码
        logger.info(
            "password_reset_succeeded user_id=%s user_name=%s", user_id, row.user_name
        )
        return True, RESET_SUCCESS_MESSAGE
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "reset_password"`
Expected: PASS

---

## Task 6: 请求侧编排（`request_reset`）

**Files:**
- Modify: `app/services/password_reset_service.py`
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.no_infrastructure
async def test_request_reset_returns_none_when_rate_limited(db_session, enabled_account):
    redis = FakeRedis()
    _, email = enabled_account
    first = await PasswordResetService.request_reset(db_session, email, "1.2.3.4", redis)
    assert first is not None
    second = await PasswordResetService.request_reset(db_session, email, "1.2.3.4", redis)
    assert second is None, "冷却窗口内不得再次发信"


@pytest.mark.no_infrastructure
async def test_request_reset_consumes_limits_even_for_unknown_email(db_session):
    """限流必须在查库之前生效，且对不存在的邮箱同样计数。

    若先查库、只在命中时才计数，「计数有没有被消耗」本身就变成了邮箱是否存在的观测点。
    """
    redis = FakeRedis()
    assert await PasswordResetService.request_reset(
        db_session, "nobody@nowhere.example.com", "9.9.9.9", redis
    ) is None
    assert redis.data.get(AuthService.pwd_reset_cooldown_key("nobody@nowhere.example.com"))
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "request_reset"`
Expected: FAIL — `request_reset` 不存在

- [ ] **Step 3: 实现**

```python
    @staticmethod
    async def request_reset(
        db: AsyncSession, email: str, source: str, redis
    ) -> Optional[Tuple[int, str]]:
        """限流 → 查库，返回 (user_id, 邮箱) 表示「应当发信」，否则 None。

        调用方**无论拿到什么**都必须返回同一句 UNIFIED_MESSAGE（含被限流的场景）。

        限流刻意排在查库**之前**且对不存在的邮箱同样计数：若先查库、只在命中时才计数，
        「计数有没有被消耗」本身就变成了邮箱是否存在的观测点。
        """
        if not await PasswordResetService.pass_rate_limits(email, source, redis):
            logger.info("password_reset_rate_limited source=%s", source)
            return None

        normalized = AuthService.normalize_email(email)
        if not normalized:
            return None

        row = (
            await db.execute(
                select(User.id, User.email).where(
                    func.lower(User.email) == normalized, User.status == 1
                )
            )
        ).first()
        if row is None:
            return None
        return int(row.id), str(row.email)
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "request_reset"`
Expected: PASS

---

## Task 7: 两个端点 + 公开配置字段

**Files:**
- Modify: `app/api/portal/endpoints/auth.py`（模型定义处约 `:545` 附近；端点加在 `change_password` 之后）
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试（HTTP 层）**

```python
RESET_REQUEST_URL = "/api/portal/auth/password-reset/request"
RESET_CONFIRM_URL = "/api/portal/auth/password-reset/confirm"
PORTAL_PREFIX = "https://portal.example.com"


def _stub_mail(monkeypatch, *, available=True, prefix=PORTAL_PREFIX):
    """把「邮件服务可用性 + 对外地址 + 实际发信」变成显式可控，返回发信捕获列表。

    patch 目标只有一处：`password_reset_service` 模块名下的 `get_download_url_prefix`
    （availability 与 build_link 都经它导入）。务必不要分别 patch 端点里的局部导入，
    否则会出现「patch 了不生效、用例永远通过」的假绿。
    """
    from app.services import email_delivery_service as edmod
    from app.services import password_reset_service as prmod

    captured: list = []

    async def _settings():
        if not available:
            return None
        return edmod.SmtpSettings(
            host="g.smtp.cn", port=465, user="g@a.cn", password="pw",
            security="ssl", from_address="g@a.cn", sender_name="平台",
        )

    def _send(smtp, to, subject, body, **kwargs):
        captured.append({"to": list(to), "subject": subject, "body": body})
        return True, ""

    async def _prefix():
        return prefix

    monkeypatch.setattr(edmod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))
    monkeypatch.setattr(prmod, "get_download_url_prefix", _prefix)
    return captured


def _stub_redis(monkeypatch, redis):
    """替换端点模块里的 get_redis；传 None 即模拟 Redis 不可用。"""

    async def _get():
        return redis

    monkeypatch.setattr("app.api.portal.endpoints.auth.get_redis", _get)


async def test_request_endpoint_is_uniform_for_every_email(
    client, enabled_account, disabled_account, pending_account, monkeypatch
):
    """防枚举核心：不存在 / 待审核 / 已禁用 / 启用命中的响应必须逐字节相同。

    四个邮箱各自 60 秒冷却（按邮箱分桶），同一来源 4 次未触顶 5 次/小时，所以
    这一串请求不会被限流干扰。
    """
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())

    _, enabled_email = enabled_account
    _, disabled_email = disabled_account
    _, pending_email = pending_account

    bodies = []
    for email in ("nobody@nowhere.example.com", pending_email, disabled_email, enabled_email):
        resp = await client.post(RESET_REQUEST_URL, json={"email": email})
        assert resp.status_code == 200, resp.text
        bodies.append(resp.text)
    assert len(set(bodies)) == 1, "不同邮箱的响应体必须完全一致"


async def test_request_endpoint_503_when_mail_disabled(client, monkeypatch):
    """与邮箱无关的平台级原因 → 可以明确报错，不构成枚举泄露。"""
    _stub_mail(monkeypatch, available=False)
    _stub_redis(monkeypatch, FakeRedis())

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503
    assert "邮件找回功能未开启" in resp.json()["detail"]


async def test_request_endpoint_503_when_prefix_empty(client, monkeypatch):
    _stub_mail(monkeypatch, prefix="")
    _stub_redis(monkeypatch, FakeRedis())

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503
    assert "download_url_prefix" in resp.json()["detail"]


async def test_request_endpoint_503_when_redis_down(client, monkeypatch):
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, None)

    resp = await client.post(RESET_REQUEST_URL, json={"email": "a@b.com"})
    assert resp.status_code == 503
    assert "暂时不可用" in resp.json()["detail"]


async def test_request_endpoint_rejects_malformed_email(client, monkeypatch):
    """格式非法必须是 400，不能静默 200 —— 格式只取决于输入，不泄露任何注册信息。"""
    _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())

    resp = await client.post(RESET_REQUEST_URL, json={"email": "not-an-email"})
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"]


async def test_request_endpoint_sends_mail_with_prefix_link(
    client, enabled_account, monkeypatch
):
    """发信发生在响应之后，且链接由 download_url_prefix 拼出。"""
    sent = _stub_mail(monkeypatch)
    _stub_redis(monkeypatch, FakeRedis())
    _, email = enabled_account

    resp = await client.post(RESET_REQUEST_URL, json={"email": email})
    assert resp.status_code == 200
    assert sent, "BackgroundTasks 在响应后执行；TestClient 下此刻应已完成"
    assert sent[0]["to"] == [email]
    assert f"{PORTAL_PREFIX}/reset-password?token=" in sent[0]["body"]


async def test_confirm_endpoint_rejects_bad_token_and_accepts_good_one(
    client, enabled_account, monkeypatch
):
    from app.services.password_reset_service import PasswordResetService

    redis = FakeRedis()
    _stub_redis(monkeypatch, redis)
    uid, _ = enabled_account

    bad = await client.post(
        RESET_CONFIRM_URL, json={"token": "999.deadbeef", "password": "Str0ng#Passw0rd"}
    )
    assert bad.status_code == 400
    assert "链接无效或已过期" in bad.json()["detail"]

    token = await PasswordResetService.issue_token(uid, redis)
    ok = await client.post(
        RESET_CONFIRM_URL, json={"token": token, "password": "Str0ng#Passw0rd"}
    )
    assert ok.status_code == 200, ok.text
    assert "重置成功" in ok.json()["message"]
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "endpoint"`
Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 实现**

```python
class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str
```

```python
@router.post("/password-reset/request", summary="发起密码找回（发送重置邮件）")
async def request_password_reset(
    payload: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """未登录可用的密码找回入口。

    顺序即错误优先级：邮件服务 → 对外地址 → Redis → 限流 → 格式 → 查库 → 发信。
    除「与提交的邮箱无关」的平台级错误外，**一律返回同一句文案**（防枚举）。
    """
    from app.services.password_reset_service import PasswordResetService, UNIFIED_MESSAGE

    # 1-2. 功能是否可用（邮件服务 + 对外地址）。判定与提交的邮箱无关，
    #      因此可以明确报错而不构成枚举泄露；两个原因分开是为了让管理员知道该去配什么。
    unavailable = await PasswordResetService.availability()
    if unavailable:
        raise HTTPException(503, unavailable)

    # 3. Redis 必须可用：发信要 fail-closed，不能发一封之后无法核销的链接
    redis = await get_redis()
    if not redis:
        raise HTTPException(503, "密码重置服务暂时不可用，请稍后重试")

    # 4. 限流（在格式校验之前，与注册端点「限流先于格式校验」的不变式一致）
    source = AuthService.resolve_rate_limit_source(http_request)
    target = await PasswordResetService.request_reset(db, payload.email, source, redis)

    # 5. 格式校验：只取决于输入本身，因此可以明确报错而不产生枚举信息
    if target is None and AuthService.normalize_email(payload.email) is None:
        raise HTTPException(400, "邮箱格式不正确")

    # 6. 需要发信时：token 同步签发（保证链接一定可核销），SMTP 放到响应之后
    if target is not None:
        user_id, email = target
        link = await PasswordResetService.build_link(user_id, redis)
        if link:
            subject, body = PasswordResetService.build_mail(link)
            background_tasks.add_task(
                PasswordResetService.deliver, email, subject, body
            )

    return {"status": "success", "message": UNIFIED_MESSAGE}


@router.post("/password-reset/confirm", summary="用邮件链接重置密码")
async def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db_session),
):
    from app.services.password_reset_service import PasswordResetService

    redis = await get_redis()
    if not redis:
        raise HTTPException(503, "密码重置服务暂时不可用，请稍后重试")

    ok, message = await PasswordResetService.reset_password(
        payload.token, payload.password, db, redis
    )
    if not ok:
        raise HTTPException(400, message)
    return {"status": "success", "message": message}
```

**同时必须补两个导入**（`auth.py` 当前**没有**它们，漏掉就是 `NameError`）：

```python
from fastapi import APIRouter, Depends, HTTPException, status, Response, Header, Request, BackgroundTasks
from app.core.redis import get_redis
```

`Request` 与 `APIRouter` 等已在文件中，只需在既有 `from fastapi import ...` 那一行**追加 `BackgroundTasks`**，
并新增 `from app.core.redis import get_redis`。

其中 `PasswordResetService.deliver` 是后台任务（**必须自己捕获全部异常**，后台任务的异常
不会被响应吃掉）：

```python
    @staticmethod
    async def deliver(email: str, subject: str, body: str) -> None:
        """后台发信：任何异常只记日志。

        响应必须与「有没有真的发出去」无关 —— 反枚举要求响应不能报告真实发送结果。
        """
        from app.services.email_delivery_service import EmailDeliveryService

        try:
            settings = await EmailDeliveryService.load_global_smtp_settings()
            if settings is None:
                logger.warning("密码重置邮件未发送：全局邮件服务不可用")
                return
            ok, message = await asyncio.to_thread(
                EmailDeliveryService.send_mail, settings, [email], subject, body
            )
            if not ok:
                logger.warning("密码重置邮件发送失败：%s", message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("密码重置邮件发送异常：%s", exc)
```

同时修改 `/config/public`：

```python
    # 密码找回入口是否可见：与 request 端点用**同一个** availability() 判定，
    # 避免出现「入口显示了但接口 503」这种自相矛盾的状态。
    password_reset_available = False
    try:
        from app.services.password_reset_service import PasswordResetService

        password_reset_available = (await PasswordResetService.availability()) is None
    except Exception as exc:  # noqa: BLE001
        logger.warning("计算 password_reset_available 失败：%s", exc)
```

并在返回字典里加 `"password_reset_available": password_reset_available,`。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "endpoint"`
Expected: PASS

---

## Task 8: `PUT /auth/password` 改密后吊销会话

**Files:**
- Modify: `app/api/portal/endpoints/auth.py`（`change_password`，约 `:548-577`）
- Test: `tests/test_password_reset.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_change_password_revokes_all_sessions(client, valid_api_key, monkeypatch):
    """改密必须让所有设备重新登录；否则旧密码泄露后改密也踢不掉对方。"""
    revoked = []

    async def _revoke(user_id, redis=None):
        revoked.append(int(user_id))
        return 1

    monkeypatch.setattr(AuthService, "revoke_sessions_for_user", staticmethod(_revoke))

    resp = await client.put(
        "/api/portal/auth/password",
        json={"password": "Str0ng#Passw0rd"},
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200
    assert revoked == [<该 fixture 用户的 id>]
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py::test_change_password_revokes_all_sessions -q`
Expected: FAIL — `revoked == []`

- [ ] **Step 3: 实现**

在 `change_password` 的 `set_user_password` 成功分支里加：

```python
    if success:
        # 改密后吊销该用户全部会话：否则「旧密码已泄露 → 改密」之后，
        # 对方手里的会话依然有效，改密等于没改。当前设备同样会被登出，
        # 这是预期行为，前端在成功提示后清凭据并跳登录页。
        try:
            await AuthService.revoke_sessions_for_user(user_id)
        except Exception as exc:  # noqa: BLE001
            # 吊销失败不能把「密码已改成功」变成 500 —— 密码确实已经改了
            logger.warning("改密后吊销会话失败 user_id=%s: %s", user_id, exc)
        return {"status": "success", "message": "密码修改成功"}
```

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest tests/test_password_reset.py -q -k "revokes_all_sessions"`
Expected: PASS

---

## Task 9: 前端重置页与路由

**Files:**
- Create: `frontend/src/views/ResetPassword.vue`
- Modify: `frontend/src/router/index.ts`
- Test: `tests/frontend/test_password_reset_contract.py`

- [ ] **Step 1: 写失败契约测试**

```python
"""契约：登录页找回入口、公开重置路由、重置页两种模式、统一文案。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGIN = (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")
ROUTER = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
RESET = (ROOT / "frontend/src/views/ResetPassword.vue").read_text(encoding="utf-8")


def test_reset_route_is_public():
    assert "path: '/reset-password'" in ROUTER
    idx = ROUTER.index("path: '/reset-password'")
    assert "public: true" in ROUTER[idx: idx + 300]


def test_login_entry_is_gated_by_public_flag():
    assert "passwordResetAvailable" in LOGIN
    assert "password_reset_available" in LOGIN


def test_reset_page_has_two_modes():
    assert "route.query.token" in RESET
    assert "password-reset/request" in RESET
    assert "password-reset/confirm" in RESET


def test_reset_page_does_not_hardcode_a_competing_message():
    """文案以服务端返回为准，前端不得另写一句不同的成功提示而漂移。"""
    assert "response.data.message" in RESET
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_password_reset_contract.py -q`
Expected: FAIL — 文件不存在 / 断言失败

- [ ] **Step 3: 实现**

路由追加（放在 `/login` 之后）：

```ts
    {
      path: '/reset-password',
      name: 'ResetPassword',
      component: () => import('../views/ResetPassword.vue'),
      meta: { public: true, title: '重置密码' }
    },
```

`ResetPassword.vue` 要点（沿用 Login.vue 的视觉与 `showToast` 习惯）：

- 用 `useRoute()` 读 `route.query.token`；`hasToken = computed(() => !!route.query.token)`
- **无 token**：邮箱输入框 + 提交按钮 → `POST /api/portal/auth/password-reset/request`
  → 成功后把 `response.data.message` 原样展示，并启动 60 秒按钮倒计时（本地冷却，**不依赖服务端报错**）
- **有 token**：新密码 + 确认密码 → 客户端校验一致性 → `POST .../confirm`
  → 成功展示 `response.data.message` 并 `router.push('/login')`
- 失败：优先展示 `error.response?.data?.detail`
- token 失效时提供「重新申请」按钮切回邮箱模式（`router.replace('/reset-password')`）
- 「已发送」态里补一句静态提示：「如果长时间未收到，请检查邮箱地址是否正确，并查看垃圾邮件」
  —— 这是防枚举取舍的必要补偿：用户填错邮箱时服务端**故意**不会告诉他（见规范 §12）

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_password_reset_contract.py -q`
Expected: PASS

---

## Task 10: 登录页入口 + 个人中心改密联动

**Files:**
- Modify: `frontend/src/views/Login.vue`
- Modify: `frontend/src/views/PersonalCenter.vue`（承载改密表单）
- Test: `tests/frontend/test_password_reset_contract.py`

- [ ] **Step 1: 写失败契约测试**

```python
def test_login_shows_entry_only_when_available():
    # 入口必须受开关控制，不能无条件渲染
    idx = LOGIN.index("忘记密码")
    assert "v-if=\"passwordResetAvailable\"" in LOGIN[max(0, idx - 400): idx + 200]


def test_login_reads_flag_from_public_config():
    idx = LOGIN.index("password_reset_available")
    assert "config/public" in LOGIN


def test_change_password_success_logs_out_current_device():
    src = (ROOT / "frontend/src/views/PersonalCenter.vue").read_text(encoding="utf-8")
    idx = src.index("密码修改成功")
    window = src[max(0, idx - 1500): idx + 1500]
    assert "/login" in window, "改密成功后必须跳登录页（会话已被服务端吊销）"
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_password_reset_contract.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

`Login.vue`：

```ts
const passwordResetAvailable = ref(false)
// 与 registrationEnabled 同处赋值（/api/portal/auth/config/public 的回调里）：
passwordResetAvailable.value = response.data.data?.password_reset_available === true
```

模板（密码登录区）：

```html
<div v-if="passwordResetAvailable" class="text-center pt-1">
  <router-link to="/reset-password" class="text-xs text-primary hover:underline">
    忘记密码？
  </router-link>
</div>
```

个人中心改密成功分支：清本地凭据（与现有登出逻辑一致）→ `showToast("密码已修改，请重新登录", "success")` → 跳 `/login`。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_password_reset_contract.py -q`
Expected: PASS

---

## Task 11: `download_url_prefix` 说明与搜索扩展

**Files:**
- Modify: `frontend/src/views/SystemConfig.vue`（约 `:2463` 短说明、`:4511` 自定义渲染、`:898` 搜索）
- Modify: `frontend/src/components/system/DeploymentChecklist.vue:42`
- Test: `tests/frontend/test_password_reset_contract.py`、`tests/frontend/test_download_url_prefix_config_contract.py`

- [ ] **Step 1: 写失败测试**

```python
def test_download_url_prefix_description_mentions_reset_link():
    src = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
    assert "重置链接" in src or "找回密码" in src.replace("忘记密码", "找回密码")
    # 短说明与示例框都要提到
    assert src.count("重置") >= 2


def test_config_search_covers_short_descriptions():
    """搜索只覆盖数据库 description 时，只在代码里写的新说明会搜不到。"""
    src = (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")
    idx = src.index("const match =")
    window = src[idx: idx + 400]
    assert "configShortDescriptions[item.key]" in window
```

- [ ] **Step 2: 运行，确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_password_reset_contract.py -q -k "download_url_prefix or search"`
Expected: FAIL

- [ ] **Step 3: 实现**

1. `configShortDescriptions.download_url_prefix` 改为：

```ts
  download_url_prefix: '生成文件下载链接、以及忘记密码「重置链接」使用的公网地址前缀。只填协议 + 域名（如 https://your-domain.example.com），不要填写任何路径、文件名或 token。留空时回退 APP_PUBLIC_URL。',
```

2. 💡 示例框补一条（保留原有下载示例与「不要填写」警告）：

```html
<div class="mt-1">忘记密码时发出的重置邮件也会用此前缀，链接形如 <code class="font-mono text-blue-800">https://your-domain.example.com/reset-password?token=...</code>；同样<strong>不要</strong>在前缀里填写 <code class="font-mono text-blue-800">/reset-password</code> 或 token。</div>
```

3. 搜索条件（约 `:896-898`）：

```ts
    const match =
      item.key.toLowerCase().includes(q) ||
      (item.description || '').toLowerCase().includes(q) ||
      // 代码里的短说明也是用户可见文案，且本项的新说明就写在代码里（无迁移），
      // 不纳入搜索会导致「搜『找回密码』找不到这个配置」
      (configShortDescriptions[item.key] || '').toLowerCase().includes(q) ||
```

4. `DeploymentChecklist.vue` 该行说明补一句「该前缀同时用于忘记密码的重置链接」。

- [ ] **Step 4: 运行，确认通过**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: PASS（含既有 `test_download_url_prefix_config_contract.py`）

---

## Task 12: 补齐测试、反向验证与全量回归

**Files:**
- Modify: `tests/test_password_reset.py`
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 补完 §11.1 的全部 19 项后端用例**

对照规范 §11.1 逐条核对，特别是尚未覆盖的：
- 并发 confirm（同一 token 两个请求恰好一个成功）
- TTL 过期后 confirm → 400（用缩短 TTL 或直接删键模拟）
- 审计日志被写入且**不含 token 与密码**（`caplog`）
- 邮件正文含 `download_url_prefix` 拼出的链接与「30 分钟」

- [ ] **Step 2: 记录改动前的类型检查基线**

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json 2>&1 | grep -c "error TS"`
Expected: 记录该数字（截至 2026-10-01 为 **62**）

- [ ] **Step 3: 反向验证（每条守卫都要做）**

逐条注入变异 → 确认对应用例**失败** → 还原：

| 变异 | 必须失败的用例 |
|---|---|
| 统一文案改成区分文案（按「邮箱不存在」返回不同 message） | 防枚举一致性 |
| 被限流时返回「操作过于频繁」 | 限流响应一致性 |
| `GETDEL` 换成 `GET`（不删键） | 单次使用 |
| 复杂度校验挪到核销之后 | 弱密码不烧链接 |
| 去掉覆盖写（改用 `SET NX`） | 新申请作废旧链接 |
| 后台发信改成同步发信 | 链接与后台任务断言 |
| 去掉 `revoke_sessions_for_user` | 会话吊销 |
| 去掉空前缀判定 | 前缀为空 503 |
| 把格式非法改成静默 200 | 格式非法 400 |
| 搜索去掉 `configShortDescriptions` | 搜索扩展契约 |

还原后用 `diff -q` 与备份比对，确认**逐字节一致**、无变异残留。

- [ ] **Step 4: 全量回归**

```bash
.venv/bin/python -m pytest tests/test_password_reset.py tests/test_user_email_and_mail_service.py tests/test_user_registration.py -q
.venv/bin/python -m pytest tests/api tests/core -q
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q
cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Expected：全部通过；`vue-tsc` 错误数**不超过** Step 2 的基线，且无一条提到本特性的新标识符。

- [ ] **Step 5: 确认零迁移**

Run: `git status --short db-prod db-prod-pg`
Expected：**无输出**（不新增任何 SQL 文件）

- [ ] **Step 6: 更新 `tests/CHECKLIST.md`**

按现有表格新增一行（5 列 6 个竖线），记录：特性名、涉及文件、设计要点与测试闭环（含反向验证）、验收状态、日期 `2026-10-01`。

- [ ] **Step 7: 汇报，等待用户指令再提交**

列出改动文件与验证结果，并提醒用户：**重新构建前端 + 重启后端**后才生效；提交与否由用户决定。

---

## 完成标准

- [ ] 规范 §13 的 12 条验收标准全部满足
- [ ] `db-prod/` 与 `db-prod-pg/` 无新增文件
- [ ] 后端新用例 + 既有回归全绿；`vue-tsc` 零新增错误
- [ ] 每条守卫都有一次「变异后失败」的反向验证记录
- [ ] `tests/CHECKLIST.md` 已更新
