# 账号自主注册申请与管理员审核 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在登录页提供可开关的账号注册申请入口，申请提交后账号为待审核状态且不能登录，管理员在用户管理页看到待审核数量并在「待审核」页签里审核为启用或禁用。

**Architecture:** 复用 `User.status`，新增 `2 = 待审核`（不加字段、不做 DDL）；新增一个 `system_configs` 配置项 `user_registration_enabled` 控制入口显隐；注册端点挂在唯一无需鉴权的 `auth.router` 下，复用登录失败计数那套 Redis 限流；审核复用 `PATCH /users/{id}/status` 并扩展可选 `role_ids`。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Pydantic 2 + MySQL/PostgreSQL；Vue 3 + TypeScript + Tailwind。

**Spec:** `docs/superpowers/specs/2026-09-30-user-self-registration-design.md`

**仓库约定（必须遵守）:**
- 只跑测试与类型检查，**不执行** `./dev.sh`、部署脚本或生产数据库操作。
- **不执行** `git commit`，由用户自行提交。计划中的「提交」步骤一律**跳过**，改为汇报改动文件清单。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `db-prod/V162-add-user-self-registration-config.sql`（新建） | MySQL 配置种子 |
| `db-prod-pg/V63-add-user-self-registration-config.sql`（新建） | PostgreSQL 配置种子 |
| `app/models/user.py`（改） | 用户状态常量（唯一定义处）+ 列注释 |
| `app/services/config_service.py`（改） | `user_registration_enabled` 配置键常量与取值校验 |
| `app/services/auth_service.py`（改） | `generate_api_key` 支持 `status`；待审核登录分支；注册限流三件套 |
| `app/api/portal/endpoints/auth.py`（改） | `POST /register`、公开配置下发、登录 `pending_review` 分支 |
| `app/api/portal/endpoints/management.py`（改） | `UpdateStatusRequest.role_ids` + 取值/角色校验；`GET /users/pending-count` |
| `frontend/src/views/Login.vue`（改） | 申请入口 + 注册视图 |
| `frontend/src/views/SystemConfig.vue`（改） | 常规设置分组的新开关 |
| `frontend/src/views/Users.vue`（改） | 待审核页签 + 徽章 + 审核卡片列表 + 审核确认框 |
| `tests/test_user_registration_migration_contract.py`（新建） | 迁移契约 |
| `tests/test_user_registration.py`（新建） | 注册 / 审核 / 登录后端行为 |
| `tests/frontend/test_user_registration_contract.py`（新建） | 前端源码契约 |

---

### Task 1: 双库配置种子迁移

**Files:**
- Create: `db-prod/V162-add-user-self-registration-config.sql`
- Create: `db-prod-pg/V63-add-user-self-registration-config.sql`
- Test: `tests/test_user_registration_migration_contract.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_mysql_seed_exists_and_disables_registration():
    sql = _read("db-prod/V162-add-user-self-registration-config.sql")
    assert "user_registration_enabled" in sql
    assert "'false'" in sql
    assert "'general'" in sql
    assert "INSERT IGNORE" in sql.upper()
    # 全新配置项：只做 INSERT IGNORE，不出现任何 UPDATE 子句
    assert "ON DUPLICATE KEY UPDATE" not in sql.upper()


def test_pg_seed_exists_and_disables_registration():
    sql = _read("db-prod-pg/V63-add-user-self-registration-config.sql")
    assert "user_registration_enabled" in sql
    assert "'false'" in sql
    assert "'general'" in sql
    assert "ON CONFLICT" in sql.upper()
    assert "DO NOTHING" in sql.upper()
    assert "DO UPDATE" not in sql.upper()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_user_registration_migration_contract.py -v`
Expected: FAIL — `FileNotFoundError`

- [ ] **Step 3: 写迁移**

见 spec 4.1 的两段 SQL 全文。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_user_registration_migration_contract.py -v`
Expected: 2 passed

---

### Task 2: 状态常量与配置校验

**Files:**
- Modify: `app/models/user.py:23`
- Modify: `app/services/config_service.py:37-85`
- Test: `tests/test_user_registration.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from app.services.config_service import (
    USER_REGISTRATION_ENABLED_KEY,
    validate_config_update,
)
from app.models.user import (
    USER_STATUS_DISABLED,
    USER_STATUS_ENABLED,
    USER_STATUS_PENDING_REVIEW,
)


def test_status_constants():
    assert (USER_STATUS_DISABLED, USER_STATUS_ENABLED, USER_STATUS_PENDING_REVIEW) == (0, 1, 2)


@pytest.mark.parametrize("value", ["true", "false", "1", "0", "on", "off"])
def test_registration_switch_accepts_boolean_literals(value):
    validate_config_update(USER_REGISTRATION_ENABLED_KEY, value)


@pytest.mark.parametrize("value", ["yes-please", "", "2"])
def test_registration_switch_rejects_other_values(value):
    with pytest.raises(ValueError):
        validate_config_update(USER_REGISTRATION_ENABLED_KEY, value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_user_registration.py -v`
Expected: FAIL — `ImportError: cannot import name 'USER_REGISTRATION_ENABLED_KEY'`

- [ ] **Step 3: 实现**

`app/models/user.py` 在 `class User` 之前加：

```python
# 用户状态：0=禁用（含审核被拒的永久禁用）、1=启用、2=待审核（注册申请后的初始态）
USER_STATUS_DISABLED = 0
USER_STATUS_ENABLED = 1
USER_STATUS_PENDING_REVIEW = 2
```

并把 `status = Column(Integer, default=1) # 1=enabled, 0=disabled` 改为
`status = Column(Integer, default=1, comment='0=disabled, 1=enabled, 2=pending_review')`。

`app/services/config_service.py` 在 `SANDBOX_IDLE_TIME_DEFAULT` 之后加：

```python
#: 账号自主注册申请开关（默认关闭）
USER_REGISTRATION_ENABLED_KEY = "user_registration_enabled"
```

`validate_config_update` 增加分支：

```python
    elif key == USER_REGISTRATION_ENABLED_KEY:
        normalized = str(value or "").strip().lower()
        if normalized not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError("user_registration_enabled 仅允许 true/false")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_user_registration.py -v`
Expected: 10 passed

---

### Task 3: auth_service 三处改动

**Files:**
- Modify: `app/services/auth_service.py:42-83`、`:662-699`、`:262-343` 附近
- Test: `tests/test_user_registration.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from app.models.user import USER_STATUS_PENDING_REVIEW
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_pending_user_cannot_login_with_password(db_session):
    from app.models.user import User
    user = User(
        user_name="pending_login_probe",
        real_name="待审核探针",
        status=USER_STATUS_PENDING_REVIEW,
        password_hash=AuthService.get_password_hash("Abcd1234!"),
    )
    db_session.add(user)
    await db_session.commit()
    result = await AuthService.verify_user_password(
        "pending_login_probe", "Abcd1234!", db=db_session
    )
    assert result["status"] == "pending_review"
    assert "审核" in result["message"]


@pytest.mark.asyncio
async def test_generate_api_key_honours_status(db_session):
    from sqlalchemy import select
    from app.models.user import User
    await AuthService.generate_api_key("status_probe", status=2, db=db_session)
    row = (await db_session.execute(
        select(User).where(User.user_name == "status_probe")
    )).scalar_one()
    assert row.status == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_user_registration.py -k "pending_user or honours_status" -v`
Expected: FAIL — `verify_user_password` 返回 `fail`；`generate_api_key()` 不认识 `status` 参数

- [ ] **Step 3: 实现**

1. `generate_api_key` 签名加 `status: int = 1`，`new_user` 的 `status=1` 改为 `status=status`。
2. `verify_user_password` 拆分状态分支（见 spec 4.6），并 import 状态常量。
3. 新增注册限流常量与三个方法，写法照抄 `record_login_failure` 那一组：

```python
    # --- 注册申请限流 ---
    # 注册是公开接口；按 IP 与账号名双维度限流，避免被脚本刷满待审核列表。
    REGISTER_IP_LIMIT = 5
    REGISTER_NAME_LIMIT = 3
    REGISTER_WINDOW_SECONDS = 3600

    @staticmethod
    def _register_ip_key(ip: str) -> str:
        return f"auth:register:ip:{(ip or 'unknown').strip()}"

    @staticmethod
    def _register_name_key(user_name: str) -> str:
        return f"auth:register:name:{(user_name or '').strip().lower()}"

    @staticmethod
    async def _register_counter_hit(key: str, limit: int) -> bool:
        """自增计数并返回是否超限。Redis 不可用时放行（fail-open）。"""
        redis = await AuthService._login_guard_redis()
        if not redis:
            return False
        try:
            count = int(await redis.incr(key))
            if count == 1:
                await redis.expire(key, AuthService.REGISTER_WINDOW_SECONDS)
            return count > limit
        except Exception as exc:
            logger.warning("注册限流计数失败，本次放行: %s", exc)
            return False

    @staticmethod
    async def is_registration_rate_limited(client_ip: str, user_name: str) -> bool:
        """IP 或账号名任一维度超限即返回 True。"""
        if await AuthService._register_counter_hit(
            AuthService._register_ip_key(client_ip), AuthService.REGISTER_IP_LIMIT
        ):
            return True
        return await AuthService._register_counter_hit(
            AuthService._register_name_key(user_name), AuthService.REGISTER_NAME_LIMIT
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_user_registration.py -k "pending_user or honours_status" -v`
Expected: 2 passed

---

### Task 4: 注册端点与公开配置

**Files:**
- Modify: `app/api/portal/endpoints/auth.py`（`get_public_config` 与新增 register）
- Test: `tests/test_user_registration.py`

- [ ] **Step 1: 写失败测试**（httpx AsyncClient 打 `POST /api/portal/auth/register`，覆盖开关关闭 403 / 格式 400 / 等保 400 / 重名 400 / 成功 status=2 / 限流 429 / 待审核登录 403，见 spec 8.2）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_user_registration.py -k register -v`
Expected: FAIL — 404

- [ ] **Step 3: 实现**（见 spec 4.3 / 4.4，含 `IntegrityError` 兜底转 400）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_user_registration.py -v`
Expected: 全绿

---

### Task 5: 用户管理端点

**Files:**
- Modify: `app/api/portal/endpoints/management.py:285-290`、`:567-605`、新增 pending-count
- Test: `tests/test_user_registration.py`

- [ ] **Step 1: 写失败测试**（`PATCH status=3` → 400；`status=1 + role_ids` → `UserRoleRelation` 落库；`role_ids=[999999]` → 400；`GET /users/pending-count` 计数正确）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_user_registration.py -k "status or pending_count" -v`

- [ ] **Step 3: 实现**（见 spec 4.7）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_user_registration.py -v`

---

### Task 6–8: 前端三处

**Files:**
- Modify: `frontend/src/views/SystemConfig.vue`
- Modify: `frontend/src/views/Login.vue`
- Modify: `frontend/src/views/Users.vue`
- Test: `tests/frontend/test_user_registration_contract.py`

- [ ] **Step 1: 写失败契约测试**（见 spec 8.1 的 7 条断言）
- [ ] **Step 2: 跑测试确认失败**：`pytest --confcutdir=tests/frontend tests/frontend/test_user_registration_contract.py -v`
- [ ] **Step 3: 实现三处改动**（见 spec 5.1 / 5.2 / 5.3）
- [ ] **Step 4: 跑测试确认通过** + `cd frontend && npx vue-tsc --noEmit -p tsconfig.app.json`

---

### Task 9: 回归与清单

- [ ] `pytest --confcutdir=tests/frontend tests/frontend/`
- [ ] `cd frontend && npx vue-tsc --noEmit -p tsconfig.app.json`（与改动前基线比对，零新增报错）
- [ ] `pytest tests/test_user_registration.py tests/test_user_registration_migration_contract.py -v`
- [ ] 追加 `tests/CHECKLIST.md` 记录
- [ ] 汇报改动文件清单（**不提交**）
