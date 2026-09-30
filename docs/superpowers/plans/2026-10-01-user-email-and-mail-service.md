# 用户邮箱字段与全平台邮件服务 实施计划

> **状态：19 个任务已全部执行完成（2026-10-01）。** 99 个步骤的勾选状态是**完成后回溯标记**的，不是实时进度；执行过程中发现的偏离与计划自身缺陷，已逐条回改到本文档正文（另见文末「自查记录」与 `tests/CHECKLIST.md` 中记录的执行期发现）。本文件现在与代码一致，可作为复盘或交接依据。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给用户资料加上可校验、全局唯一的邮箱字段，并建立全平台邮件发送服务，使管理员审核账号后能自动把结果邮件通知到用户。

**Architecture:** 四个可独立交付的阶段。A 阶段把邮箱变成一等用户字段（迁移 + 校验 + 三处入口 + 前端）；B 阶段在系统配置里新建「邮件服务」分组与一个统一的 SMTP 发送服务；C 阶段把审核动作接到该发送服务上（强制走全局配置，不阻塞审核）；D 阶段改造个人中心的通知渠道，让用户可以选择用全局还是自定义 SMTP，并默认抄送自己。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / Pydantic 2 / MySQL 与 PostgreSQL 双库迁移 / Vue 3 + TypeScript / Tailwind / pytest / vue-tsc

**依据设计:** `docs/superpowers/specs/2026-10-01-user-email-and-mail-service-design.md`

---

## 关键前置约定（每个任务都适用）

- **测试命令**：`.venv/bin/python -m pytest ...`（bare `python` 不存在）。
- **前端契约测试**：`.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q`
- **类型检查**：`cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json`（不加 `NODE_OPTIONS` 会 OOM）。基线是 62 个既有错误，**只允许一个都不新增**。
- **不要执行** `./dev.sh`、部署脚本、任何数据库写操作（AGENTS.md）。迁移只写 SQL 文件，由用户执行。
- **`registration_on` / `registration_off` / `cleanup_registered` 三个 fixture 定义在 `tests/conftest.py`**（原先在 `tests/test_user_registration.py` 模块内，加邮箱字段时上移，因为 `tests/` 不是 package，测试模块之间无法 import 彼此的辅助代码）。新测试文件直接声明同名 fixture 参数即可用。
- **本特性的迁移（`db-prod/V163`、`db-prod-pg/V64`）已由用户应用于开发库。** 若新加的列在库里不存在，所有查 `ai_agent_users` 的用例都会以 `Unknown column ... in 'field list'` 失败——这不是代码问题，而是库结构没跟上，需请用户执行迁移。
- **不要执行** `git commit`（除非用户在当次对话明确要求）。
- **运行时是 Python 3.11**，不要用 3.12+ 语法。
- 迁移编号已占用：MySQL 最大 `V162`，PG 最大 `V63`。本计划新增 MySQL `V163`/`V164`、PG `V64`/`V65`。

---

## 文件结构

**新增**

| 文件 | 职责 |
|---|---|
| `db-prod/V163-add-user-email.sql` | MySQL：email 列 + 唯一索引 |
| `db-prod-pg/V64-add-user-email.sql` | PG：同上（`IF NOT EXISTS`） |
| `db-prod/V164-add-email-service-configs.sql` | MySQL：8 条 `category='email'` 配置种子 |
| `db-prod-pg/V65-add-email-service-configs.sql` | PG：同上 |
| `app/services/email_delivery_service.py` | SMTP 设置加载、全局/自定义分层解析、发送、审核邮件文案 |
| `tests/test_user_email_and_mail_service.py` | 本特性的后端行为测试 |
| `tests/test_user_email_migration_contract.py` | 4 个迁移文件的契约测试 |
| `tests/frontend/test_user_email_contract.py` | 前端契约测试 |

**修改（后端）**

| 文件 | 改动 |
|---|---|
| `app/models/user.py` | 增 `email` 列 |
| `app/services/auth_service.py` | `normalize_email` / `is_user_email_taken`；5 处用户 dict 各加 `email` |
| `app/api/portal/endpoints/auth.py` | 注册 request/校验/落库；`/me`；新增 `PATCH /me/profile` |
| `app/api/portal/endpoints/management.py` | 创建/编辑 request+校验+落库；列表序列化与搜索；审核发信；重发端点 |
| `app/services/config_service.py` | 8 个新 key 的校验分支 |
| `app/services/notification_service.py` | `send_email` 分层解析 + `include_self` |
| `app/services/ai/tools/notification_tools.py` | 加密方式显式化（消除 STARTTLS 静默降级） |
| `app/services/user_sync_service.py`、`app/schemas/user_sync.py` | email 参与第三方同步 |

**修改（前端）**

| 文件 | 改动 |
|---|---|
| `frontend/src/api/portal.ts` | `User.email`；通知配置类型 |
| `frontend/src/views/Login.vue` | 注册表单邮箱 |
| `frontend/src/views/Users.vue` | 表单/列表/搜索；审核 toast；重发按钮 |
| `frontend/src/views/PersonalCenter.vue` | 基本信息邮箱编辑 |
| `frontend/src/components/personal/NotificationConfigs.vue` | 来源单选、全局概览、`include_self` |
| `frontend/src/views/SystemConfig.vue` | 分组注册（4 处 map/数组）+ 测试发送按钮 |
| `frontend/src/components/ThirdPartyUserSyncDrawer.vue` | email 字段映射 |

---

# 阶段 A：用户邮箱字段

## Task 1: 双库迁移

**Files:**
- Create: `db-prod/V163-add-user-email.sql`
- Create: `db-prod-pg/V64-add-user-email.sql`
- Create: `tests/test_user_email_migration_contract.py`

- [x] **Step 1: 写迁移契约测试（先失败）**

新建 `tests/test_user_email_migration_contract.py`：

```python
"""邮箱字段迁移契约：钉住双库各自的幂等写法，避免两套迁移混用或漏建唯一索引。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL = ROOT / "db-prod" / "V163-add-user-email.sql"
PG = ROOT / "db-prod-pg" / "V64-add-user-email.sql"


def test_both_migration_files_exist():
    assert MYSQL.is_file(), f"缺少 MySQL 迁移: {MYSQL}"
    assert PG.is_file(), f"缺少 PG 迁移: {PG}"


def test_mysql_adds_column_after_real_name_with_comment():
    sql = MYSQL.read_text(encoding="utf-8")
    assert "ALTER TABLE" in sql and "ai_agent_users" in sql
    assert "ADD COLUMN" in sql and "`email`" in sql
    assert "AFTER `real_name`" in sql, "email 应紧跟在 real_name 之后"
    assert "VARCHAR(254)" in sql
    assert "NULL" in sql, "email 必须允许为 NULL（多用户不填时不能互相冲突）"


def _ddl_only(sql: str) -> str:
    """剥掉 `--` 注释行，只留可执行 DDL。

    契约断言必须只看 DDL：注释里为了解释约定免不了提到 "IF NOT EXISTS"，
    把注释算进来会让一条纯文档性的改动就弄挂断言（初版就在这里踩了坑）。
    """
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def test_mysql_creates_unique_index():
    sql = MYSQL.read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX" in sql
    assert "uk_ai_agent_users_email" in sql
    # MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS，幂等由 apply_sql.py 吞掉 1060/1061 实现
    alter_stmt = _ddl_only(sql).split(";")[0]
    assert "ALTER TABLE" in alter_stmt
    assert "IF NOT EXISTS" not in alter_stmt


def test_pg_uses_native_if_not_exists():
    sql = PG.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in sql, "PG 必须用原生 IF NOT EXISTS 保持真幂等"
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
    assert "uk_ai_agent_users_email" in sql
    assert '"email"' in sql and "VARCHAR(254)" in sql


def test_pg_does_not_mix_mysql_syntax():
    sql = PG.read_text(encoding="utf-8")
    assert "`" not in sql, "PG 迁移不能出现 MySQL 反引号"
    assert "INSERT IGNORE" not in sql
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_migration_contract.py -q`
Expected: FAIL，`缺少 MySQL 迁移` 断言失败

- [x] **Step 3: 写 MySQL 迁移**

`db-prod/V163-add-user-email.sql`：

```sql
-- V163: 用户资料增加邮箱字段
-- 用途：注册/管理员增改/个人中心自助编辑，并作为审核结果通知的投递地址
-- 说明：MySQL 8.0 不支持 ADD COLUMN IF NOT EXISTS；重复执行时 apply_sql.py 会吞掉
--       ERROR 1060(列已存在)/1061(索引已存在)，因此本文件可安全重跑。
--       email 允许 NULL：唯一索引在多 NULL 下不冲突，所以未填写邮箱的用户可以很多。

ALTER TABLE `ai_agent_users`
  ADD COLUMN `email` VARCHAR(254) NULL COMMENT '邮箱（小写归一化存储，全局唯一；NULL=未填写）' AFTER `real_name`;

CREATE UNIQUE INDEX `uk_ai_agent_users_email` ON `ai_agent_users` (`email`);
```

- [x] **Step 4: 写 PG 迁移**

`db-prod-pg/V64-add-user-email.sql`：

```sql
-- V64: 用户资料增加邮箱字段（对应 MySQL V163）
-- PG 原生支持 IF NOT EXISTS，因此无需依赖错误码吞掉，真正幂等。

ALTER TABLE "ai_agent_users"
    ADD COLUMN IF NOT EXISTS "email" VARCHAR(254) NULL;

COMMENT ON COLUMN "ai_agent_users"."email" IS '邮箱（小写归一化存储，全局唯一；NULL=未填写）';

CREATE UNIQUE INDEX IF NOT EXISTS "uk_ai_agent_users_email"
    ON "ai_agent_users" ("email");
```

- [x] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_migration_contract.py -q`
Expected: PASS（5 passed）

## Task 2: 模型 + 归一化 + 邮箱查重

**Files:**
- Modify: `app/models/user.py`（在 `real_name` 之后加一列）
- Modify: `app/services/auth_service.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

新建 `tests/test_user_email_and_mail_service.py`：

```python
"""用户邮箱字段与邮件服务的行为测试。

约定：纯逻辑用例标 no_infrastructure；涉及库的用例复用 conftest 的 client/db_session。
"""
import pytest

from app.services.auth_service import AuthService

pytestmark = []


# --------------------------------------------------------------------------- #
# 邮箱归一化（纯单元）
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  User@Example.COM  ", "user@example.com"),
        ("a.b+c@sub.domain.cn", "a.b+c@sub.domain.cn"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_email_normalizes_or_blanks(raw, expected):
    assert AuthService.normalize_email(raw) == expected


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "raw",
    [
        "no-at-sign",
        "no-dot@localhost",
        "two@@at.com",
        "has space@example.com",
        "@example.com",
        "user@",
        "user@" + "a" * 250 + ".com",   # 超长
    ],
)
def test_normalize_email_raises_on_invalid(raw):
    with pytest.raises(ValueError):
        AuthService.normalize_email(raw, strict=True)


@pytest.mark.no_infrastructure
def test_normalize_email_lenient_mode_returns_none_for_invalid():
    """非 strict 模式用于「用户还没输完」的场景，不抛异常。"""
    assert AuthService.normalize_email("no-at-sign") is None
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q`
Expected: FAIL，`AttributeError: type object 'AuthService' has no attribute 'normalize_email'`

- [x] **Step 3: 加模型列**

`app/models/user.py`，在 `real_name` 那行之后插入：

```python
    email = Column(String(254), nullable=True, index=True, comment='邮箱（小写归一化存储，全局唯一；NULL=未填写）')
```

- [x] **Step 4: 实现归一化与查重**

在 `app/services/auth_service.py` 中 `is_user_name_taken` 之后插入：

```python
    # 邮箱的格式上限与宽松正则。刻意不做 RFC 5322 全量校验：
    # 过度严格会误伤合法地址（带引号的本地部分、IDN 域名等），而平台并不真的投递到
    # 任意合法地址——真正能发现写错的办法只有发信验证，那是已决策不做的事。
    EMAIL_MAX_LENGTH = 254
    _EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @staticmethod
    def normalize_email(raw: Optional[str], strict: bool = False) -> Optional[str]:
        """归一化邮箱：去空白 → 空串转 None → 小写。

        strict=True 时格式非法抛 ValueError；strict=False（默认）时返回 None，
        供「用户还在输入」的场景使用，避免为了探测格式而反复抛异常。
        """
        text = (raw or "").strip().lower()
        if not text:
            return None
        if (
            len(text) > AuthService.EMAIL_MAX_LENGTH
            or not AuthService._EMAIL_RE.match(text)
        ):
            if strict:
                raise ValueError("邮箱格式不正确")
            return None
        return text

    @staticmethod
    async def is_user_email_taken(
        email: str,
        exclude_user_id: Optional[int] = None,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """邮箱是否已被占用（大小写不敏感，任意状态都算占用）。

        与 is_user_name_taken 同构：注册、管理员增改、个人中心自助编辑三处共用，
        确保判定口径一致。同样显式 lower() 比对——写入时已归一化，但手工 SQL 或
        第三方同步可能绕过应用层，且两个库的 collation 行为不同。

        exclude_user_id 用于「编辑自己」：不排除会把用户自己判成重复。
        """
        normalized = AuthService.normalize_email(email)
        if not normalized:
            return False  # 未填写不参与唯一性
        session, is_local = await AuthService._get_session(db)
        try:
            stmt = select(User.id).where(func.lower(User.email) == normalized)
            if exclude_user_id is not None:
                stmt = stmt.where(User.id != exclude_user_id)
            return (await session.execute(stmt)).first() is not None
        finally:
            if is_local:
                await session.close()

    @staticmethod
    def is_email_unique_violation(exc: Exception) -> bool:
        """该 IntegrityError 是否由邮箱唯一索引引起。

        注册接口已经有一个捕获 IntegrityError 的分支（用于账号名冲突），
        不区分就会把邮箱冲突误报成「账号名已被占用」——这是本特性最容易写错的一处。
        """
        return "uk_ai_agent_users_email" in str(getattr(exc, "orig", exc))
```

确认文件顶部已 `import re`（若没有则加上）。

- [x] **Step 5: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q`
Expected: 纯单元用例全部 PASS（库相关用例所属的 Task 尚未写）

## Task 3: 注册支持邮箱（含冲突区分）

**Files:**
- Modify: `app/api/portal/endpoints/auth.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

追加到 `tests/test_user_email_and_mail_service.py`：

```python
from app.services.auth_service import AuthService as _AS

REGISTER_URL = "/api/portal/auth/register"


def _payload(name, email=None, password="Str0ng!Passw0rd"):
    body = {"user_name": name, "real_name": "邮箱测试", "password": password}
    if email is not None:
        body["email"] = email
    return body


async def test_register_accepts_optional_email(
    client, registration_on, cleanup_registered
):
    name = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.append(name)
    email = f"{name}@Example.COM"          # 大写 + 混合，验证归一化
    resp = await client.post(REGISTER_URL, json=_payload(name, email))
    assert resp.status_code == 200, resp.text

    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(User.email).where(User.user_name == name))).scalar_one()
    assert row == f"{name}@example.com".lower(), "必须小写归一化后落库"


async def test_register_without_email_still_works(
    client, registration_on, cleanup_registered
):
    name = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.append(name)
    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 200, resp.text


async def test_register_rejects_invalid_email(client, registration_on):
    resp = await client.post(REGISTER_URL, json=_payload("zmail_bad", "not-an-email"))
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"]


async def test_register_rejects_duplicate_email_case_insensitively(
    client, registration_on, cleanup_registered
):
    email = f"dup_{uuid.uuid4().hex[:8]}@example.com"
    first = f"zmail_{uuid.uuid4().hex[:8]}"
    second = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.extend([first, second])

    assert (await client.post(REGISTER_URL, json=_payload(first, email))).status_code == 200
    resp = await client.post(REGISTER_URL, json=_payload(second, email.upper()))
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"], "必须是邮箱文案，不能误报成账号名冲突"


async def test_email_conflict_is_not_reported_as_user_name_conflict(
    client, registration_on, cleanup_registered
):
    """钉住最容易写错的一处：邮箱撞唯一索引时不能报「账号名已被占用」。"""
    email = f"dup2_{uuid.uuid4().hex[:8]}@example.com"
    first = f"zmail_{uuid.uuid4().hex[:8]}"
    second = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.extend([first, second])
    assert (await client.post(REGISTER_URL, json=_payload(first, email))).status_code == 200

    resp = await client.post(REGISTER_URL, json=_payload(second, email))
    detail = resp.json()["detail"]
    assert "邮箱" in detail
    assert "账号名" not in detail
```

并在文件顶部补 import：

```python
import uuid

from sqlalchemy import select

from app.core.orm import AsyncSessionLocal
from app.models.user import User
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k email`
Expected: `test_register_accepts_optional_email` FAIL（email 未落库）

- [x] **Step 3: 给注册请求模型加字段**

`app/api/portal/endpoints/auth.py` 的 `RegisterRequest` 中，`real_name` 之后加：

```python
    email: Optional[str] = Field(None, description="邮箱，选填；填写后全局唯一")
```

- [x] **Step 4: 注册流程加校验与落库**

在 `register` 里，`real_name = ...` 之后加归一化：

```python
    # 邮箱选填。strict=True 让格式错误明确 400，而不是被静默丢弃——
    # 用户填了邮箱却因为格式问题没存上，会以为以后能收到通知。
    raw_email = (getattr(request, "email", None) or "").strip()
    try:
        email = AuthService.normalize_email(raw_email, strict=True) if raw_email else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
```

在重名检查之后、创建用户之前加邮箱唯一性校验：

```python
    if email and await AuthService.is_user_email_taken(email, db=db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=DUPLICATE_USER_EMAIL_MESSAGE,
        )
```

把 `AuthService.generate_api_key(...)` 调用加上 `email=email`（该方法的 `email` 参数由 Task 3 Step 5 补）。

> 注：真正负责建号的辅助函数是 `AuthService.generate_api_key(user_name, real_name=..., remark=..., role=..., status=..., db=...)`。
> 早先计划里写的 `create_pending_user` 并不存在，已修正。

把原有的 `except IntegrityError:` 分支改为按索引名区分：

```python
    except IntegrityError as exc:
        # 并发下两个请求同时通过唯一性检查时由唯一索引兜底。
        # 必须先判断是哪个索引冲突：注册接口同时受账号名与邮箱两个唯一键约束，
        # 一律报「账号名已被占用」会让用户改错字段、反复试不通。
        await db.rollback()
        detail = (
            DUPLICATE_USER_EMAIL_MESSAGE
            if AuthService.is_email_unique_violation(exc)
            else DUPLICATE_USER_NAME_MESSAGE
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
```

在文件常量区加：

```python
DUPLICATE_USER_EMAIL_MESSAGE = "该邮箱已被其他账号使用"
```

- [x] **Step 5: 给 generate_api_key 加 email 参数**

在 `app/services/auth_service.py` 里 `generate_api_key` 的签名加 `email: Optional[str] = None`，并在构造 `User(...)` 时加 `email=email`。

> 该方法的现有签名是 `generate_api_key(user_name, real_name=..., remark=..., role=..., status=..., db=...)`，
> 用 `grep -n "async def generate_api_key" -A 40 app/services/auth_service.py` 核对后再改。

- [x] **Step 6: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q`
Expected: 邮箱相关用例 PASS

- [x] **Step 7: 回归注册测试**

Run: `.venv/bin/python -m pytest tests/test_user_registration.py -q`
Expected: 79 passed（不得因本改动退化）

## Task 4: 管理员创建/编辑支持邮箱

**Files:**
- Modify: `app/api/portal/endpoints/management.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
USERS_URL = "/api/portal/management/users"


async def test_admin_create_user_with_email(client, admin_api_key):
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    resp = await client.post(
        USERS_URL,
        json={"user_name": name, "real_name": "管理员建", "email": email.upper()},
        headers={"X-API-Key": admin_api_key},
    )
    assert resp.status_code == 200, resp.text
    uid = resp.json().get("id") or resp.json().get("user_id")
    async with AsyncSessionLocal() as s:
        stored = (await s.execute(select(User.email).where(User.id == uid))).scalar_one()
    assert stored == email.lower()
    _cleanup(uid)


async def test_admin_create_rejects_duplicate_email(client, admin_api_key, db_session):
    email = f"adup_{uuid.uuid4().hex[:8]}@corp.example.com"
    a = f"zma_{uuid.uuid4().hex[:8]}"
    b = f"zma_{uuid.uuid4().hex[:8]}"
    ra = await client.post(USERS_URL, json={"user_name": a, "email": email},
                           headers={"X-API-Key": admin_api_key})
    assert ra.status_code == 200
    rb = await client.post(USERS_URL, json={"user_name": b, "email": email},
                           headers={"X-API-Key": admin_api_key})
    assert rb.status_code == 400
    assert "邮箱" in rb.json()["detail"]
    _cleanup(ra.json().get("id") or ra.json().get("user_id"))


async def test_admin_edit_keeps_own_email_without_conflict(client, admin_api_key):
    """编辑自己时未改邮箱不能判成重复。"""
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    r = await client.post(USERS_URL, json={"user_name": name, "email": email},
                          headers={"X-API-Key": admin_api_key})
    uid = r.json().get("id") or r.json().get("user_id")
    upd = await client.put(f"{USERS_URL}/{uid}", json={"real_name": "改名", "email": email},
                           headers={"X-API-Key": admin_api_key})
    assert upd.status_code == 200, upd.text
    _cleanup(uid)
```

并在测试文件里加清理助手（复用注册测试的清理思路）：

```python
def _cleanup(uid):
    """同步清理单个用户；用独立的短生命周期事件循环避免污染 async 测试。"""
    if uid is None:
        return
    async def _run():
        from sqlalchemy import delete as _delete
        from app.models.user_role_relation import UserRoleRelation
        async with AsyncSessionLocal() as s:
            await s.execute(_delete(UserRoleRelation).where(UserRoleRelation.user_id == uid))
            await s.execute(_delete(User).where(User.id == uid))
            await s.commit()
    asyncio.run(_run())
```

（若 `UserRoleRelation` 的实际模型模块路径不同，用 `grep -rn "class UserRoleRelation" app/models/` 确认后修正 import。）

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k admin`
Expected: FAIL

- [x] **Step 3: 请求模型加字段**

`CreateUserRequest` 与 `UpdateUserRequest` 各加：

```python
    email: Optional[str] = None
```

- [x] **Step 4: 创建加校验与落库**

`create_user` 中，在账号名唯一性检查之后加：

```python
    raw_email = (getattr(request, "email", None) or "").strip()
    try:
        email = AuthService.normalize_email(raw_email, strict=True) if raw_email else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if email and await AuthService.is_user_email_taken(email, db=db):
        raise HTTPException(status_code=400, detail="该邮箱已被其他账号使用")
```

构造 `User(...)` 时加 `email=email`。

- [x] **Step 5: 编辑加校验与落库，排除自己**

`update_user` 中加：

```python
    email_provided = getattr(request, "email", None) is not None
    if email_provided:
        raw_email = (request.email or "").strip()
        try:
            email = AuthService.normalize_email(raw_email, strict=True) if raw_email else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # 排除自己：否则「不改邮箱直接保存」会被判成与本人重复
        if email and await AuthService.is_user_email_taken(
            email, exclude_user_id=user_id, db=db
        ):
            raise HTTPException(status_code=400, detail="该邮箱已被其他账号使用")
        target.email = email
```

> ⚠️ 必须用 `email_provided`（字段是否出现在请求里）而不是 `if email:` 判定，否则管理员无法通过编辑**清空**邮箱。

- [x] **Step 6: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k admin`
Expected: PASS

## Task 5: 列表序列化、搜索、`/me` 的 5 处 dict

**Files:**
- Modify: `app/api/portal/endpoints/management.py`
- Modify: `app/services/auth_service.py`
- Modify: `app/api/portal/endpoints/auth.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
async def test_user_list_returns_email_and_searches_by_it(client, admin_api_key):
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"findme_{uuid.uuid4().hex[:8]}@corp.example.com"
    r = await client.post(USERS_URL, json={"user_name": name, "email": email},
                          headers={"X-API-Key": admin_api_key})
    uid = r.json().get("id") or r.json().get("user_id")

    listing = await client.get(USERS_URL, params={"search": email},
                               headers={"X-API-Key": admin_api_key})
    assert listing.status_code == 200
    items = listing.json().get("items", listing.json())
    assert any(i["email"] == email for i in items), "必须能按邮箱搜索并返回 email 字段"
    _cleanup(uid)


async def test_me_returns_email(client, valid_api_key, db_session):
    resp = await client.get("/api/portal/auth/me", headers={"X-API-Key": valid_api_key})
    assert resp.status_code == 200
    assert "email" in resp.json(), "/me 必须返回 email（AuthService 里 5 处 dict 都要补）"
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k "list_returns or me_returns"`
Expected: FAIL

- [x] **Step 3: 列表序列化与搜索**

`management.py` 的搜索条件（`:380` 附近）改为：

```python
        stmt = stmt.where(
            (User.user_name.like(f"%{search}%"))
            | (User.real_name.like(f"%{search}%"))
            | (User.email.like(f"%{search}%"))
        )
```

序列化 dict（`:404` 附近）加：

```python
            "email": user.email or "",
```

- [x] **Step 4: 补 AuthService 的 5 处用户 dict**

对 `app/services/auth_service.py` 中第 `:172 / :708 / :905 / :1035 / :1205` 附近的 5 处构造点，在 `"real_name": ...` 之后各加一行：

```python
            "email": user.email or "",
```

用这条命令确认 5 处都补上了（应为 5）：

```bash
grep -c '"email": user.email or ""' app/services/auth_service.py
```

- [x] **Step 5: `/me` 返回 email**

`auth.py` 的 `get_current_user_info` 返回 dict 中加：

```python
        "email": user.get("email", ""),
```

- [x] **Step 6: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k "list_returns or me_returns"`
Expected: PASS

## Task 6: 个人中心自助编辑邮箱

**Files:**
- Modify: `app/api/portal/endpoints/auth.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
PROFILE_URL = "/api/portal/auth/me/profile"


async def test_user_can_update_own_email(client, db_session):
    name = f"zself_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    api_key = await _seed_user(name, email=email)
    resp = await client.patch(PROFILE_URL, json={"email": email.upper()},
                              headers={"X-API-Key": api_key})
    assert resp.status_code == 200
    assert resp.json()["email"] == email
    await _drop_user(name)


async def test_user_cannot_take_another_users_email(client, db_session):
    taken = f"taken_{uuid.uuid4().hex[:8]}@corp.example.com"
    owner = f"zself_{uuid.uuid4().hex[:8]}"
    other = f"zself_{uuid.uuid4().hex[:8]}"
    await _seed_user(owner, email=taken)
    other_key = await _seed_user(other)
    resp = await client.patch(PROFILE_URL, json={"email": taken},
                              headers={"X-API-Key": other_key})
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"]
    await _drop_user(owner)
    await _drop_user(other)


async def test_user_can_clear_own_email(client, db_session):
    name = f"zself_{uuid.uuid4().hex[:8]}"
    key = await _seed_user(name, email=f"{name}@corp.example.com")
    resp = await client.patch(PROFILE_URL, json={"email": None},
                              headers={"X-API-Key": key})
    assert resp.status_code == 200
    assert resp.json()["email"] == ""
    await _drop_user(name)


async def test_profile_endpoint_ignores_other_fields(client, db_session):
    """最小权限：只接受 email，real_name/role/status 一律不生效。"""
    name = f"zself_{uuid.uuid4().hex[:8]}"
    key = await _seed_user(name)
    resp = await client.patch(
        PROFILE_URL,
        json={"email": f"{name}@corp.example.com", "real_name": "篡改", "role": "admin"},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(User.real_name, User.role).where(User.user_name == name)
        )).one()
    assert row[0] != "篡改" and row[1] != "admin", "其他字段必须被忽略"
    await _drop_user(name)
```

配套助手（`AuthService.generate_api_key` **自己生成并存储 hash/encrypted，只返回明文 key**，不要写成三元组解构）：

```python
async def _seed_user(user_name, email=None):
    """建一个启用状态的用户并返回其明文 API Key。

    注意 generate_api_key 的返回语义：它内部生成 api_key/encrypted/hash 并自行落库，
    只把**明文 key** 返回给调用方（签名标注 -> str）。早期计划版本误写成
    `api_key, key_hash, encrypted = ...` 三元组解构，与实际不符。
    """
    async with AsyncSessionLocal() as s:
        return await AuthService.generate_api_key(
            user_name, real_name="自助测试", status=1, email=email, db=s
        )


async def _drop_user(user_name):
    async with AsyncSessionLocal() as s:
        uid = (await s.execute(select(User.id).where(User.user_name == user_name))).scalar()
        if uid is not None:
            from sqlalchemy import delete as _delete
            await s.execute(_delete(User).where(User.id == uid))
            await s.commit()
```

（`AuthService.generate_api_key` 的确切返回签名见 `tests/test_user_registration.py` 里的现有用法，照抄即可。）

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k "own_email or another_users or clear_own or ignores_other"`
Expected: FAIL（404）

- [x] **Step 3: 实现端点**

`app/api/portal/endpoints/auth.py` 在 `/me` 路由之后加：

```python
class ProfileUpdateRequest(BaseModel):
    """个人中心自助资料更新。刻意只有一个字段——最小权限。

    设计上不允许用户自助修改 real_name / role / status / remark：
    前三个是管理属性，改 remark 没有提出需求。用独立模型而不是复用管理员那套，
    是为了让「哪些字段能自助改」这件事在类型层面就是显式的。
    """
    email: Optional[str] = None


@router.patch("/me/profile", summary="更新我的资料")
async def update_my_profile(
    request: ProfileUpdateRequest,
    user: Dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只允许改邮箱。

    不要求输入当前密码（已决策）：邮箱不是登录因子，改了也能再改；
    而要求密码确认会让纯 API Key 登录的账号无法自助修改。
    变更只打结构化日志——平台没有通用的用户变更审计表，合规审计需另立设计。
    """
    raw_email = (request.email or "").strip()
    try:
        email = AuthService.normalize_email(raw_email, strict=True) if raw_email else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    user_id = user.get("id") or user.get("user_id")
    if email and await AuthService.is_user_email_taken(email, exclude_user_id=user_id, db=db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=DUPLICATE_USER_EMAIL_MESSAGE
        )

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    old_email = target.email
    target.email = email
    try:
        await db.commit()
    except IntegrityError as exc:
        # 并发下两人同时抢同一邮箱：唯一索引兜底
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=DUPLICATE_USER_EMAIL_MESSAGE
        ) from exc

    logger.info(
        "用户自助修改邮箱: user_name=%s old=%s new=%s",
        user.get("user_name"), old_email, email,
    )
    return {"status": "success", "email": email or ""}
```

确认该文件已 import：`Dict`、`User`、`IntegrityError`、`logger`（`User` 与 `IntegrityError` 现为函数内 import，需要提到模块级或在本函数内 import）。

- [x] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k "own_email or another_users or clear_own or ignores_other"`
Expected: PASS

## Task 7: 阶段 A 前端

**Files:**
- Modify: `frontend/src/api/portal.ts`
- Modify: `frontend/src/views/Login.vue`
- Modify: `frontend/src/views/Users.vue`
- Modify: `frontend/src/views/PersonalCenter.vue`
- Test: `tests/frontend/test_user_email_contract.py`

- [x] **Step 1: 写前端契约测试（先失败）**

新建 `tests/frontend/test_user_email_contract.py`：

```python
"""前端契约：邮箱字段必须在注册/管理员/个人中心三处都露出，且移动端适配不退化。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_shared_user_type_has_email():
    src = _read("frontend/src/api/portal.ts")
    assert "email" in src


def test_register_form_has_email_input_with_mobile_classes():
    src = _read("frontend/src/views/Login.vue")
    assert 'v-model="registerForm.email"' in src or "registerForm.email" in src
    # 移动端适配类名与其它注册输入框一致
    assert "px-4 py-3 text-base" in src and "sm:py-2.5 sm:text-sm" in src


def test_register_payload_includes_email():
    src = _read("frontend/src/views/Login.vue")
    assert "email:" in src


def test_users_view_has_email_in_form_and_list():
    src = _read("frontend/src/views/Users.vue")
    assert "form.email" in src or "userForm.email" in src or ".email" in src
    assert "邮箱" in src


def test_personal_center_supports_editing_email():
    src = _read("frontend/src/views/PersonalCenter.vue")
    assert "email" in src
    assert "/me/profile" in src or "updateMyProfile" in src
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: FAIL

- [x] **Step 3: 共享类型加 email**

`frontend/src/api/portal.ts` 的 `User` 接口加 `email?: string`。

- [x] **Step 4: 注册表单加邮箱**

`Login.vue`：
- `registerForm` 加 `email: ''`
- `resetRegisterForm` 里重置 `email: ''`
- 在「用户姓名」之后插入邮箱输入框，**复用现有注册输入框的类名**（含 `px-4 py-3 text-base ... sm:py-2.5 sm:text-sm`、`autocapitalize="none" autocorrect="off" spellcheck="false"`），标签「邮箱（选填，用于接收审核结果通知）」
- 不再做输入即校验（邮箱必须全局唯一，实时探测会变成枚举接口）；只在前端做基础格式提示，真校验交给后端
- `handleRegister` 的 payload 加 `email: registerForm.email.trim() || undefined`
- 后端 400 里含「邮箱」时，把错误挂到 `registerError` 显示

- [x] **Step 5: 管理员表单与列表加邮箱**

`Users.vue`：
- 创建/编辑表单对象加 `email`，表单里加邮箱输入（放在「用户姓名」之后），编辑对话框需支持清空
- 提交 payload 带上 `email`（**编辑时即使为空也要传**，否则无法清空）
- 列表加「邮箱」列（放「用户姓名」之后），空值显示 `-`
- 搜索框 placeholder 补「邮箱」

- [x] **Step 6: 个人中心基本信息加邮箱编辑**

`PersonalCenter.vue`：
- 基本信息区加一行「邮箱」，显示当前值（空则提示「未设置」）
- 加「修改邮箱」按钮 → 小弹窗或行内编辑，输入框 + 校验提示
- 调 `axios.patch('/api/portal/auth/me/profile', { email })`，成功后刷新本地用户信息并 toast
- 失败时展示后端 detail

- [x] **Step 7: 运行契约测试 + 类型检查**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: PASS

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: 与基线逐条一致（62 个既有错误，零新增）

- [x] **Step 8: 阶段 A 回归**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py tests/test_user_email_migration_contract.py tests/test_user_registration.py tests/test_user_registration_migration_contract.py -q`
Expected: 全绿

---

# 阶段 B：全局邮件服务

## Task 8: 邮件服务配置种子 + 校验

**Files:**
- Create: `db-prod/V164-add-email-service-configs.sql`
- Create: `db-prod-pg/V65-add-email-service-configs.sql`
- Modify: `app/services/config_service.py`
- Modify: `tests/test_user_email_migration_contract.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写迁移契约测试**

追加到 `tests/test_user_email_migration_contract.py`：

```python
MYSQL_CFG = ROOT / "db-prod" / "V164-add-email-service-configs.sql"
PG_CFG = ROOT / "db-prod-pg" / "V65-add-email-service-configs.sql"

EMAIL_CONFIG_KEYS = [
    "email_service_enabled",
    "email_smtp_host",
    "email_smtp_port",
    "email_smtp_security",
    "email_smtp_user",
    "email_smtp_password",
    "email_from_address",
    "email_sender_name",
]


def test_email_config_migrations_exist():
    assert MYSQL_CFG.is_file()
    assert PG_CFG.is_file()


@pytest.mark.parametrize("path", [MYSQL_CFG, PG_CFG])
def test_email_config_seeds_all_keys_in_email_category(path):
    sql = path.read_text(encoding="utf-8")
    for key in EMAIL_CONFIG_KEYS:
        assert key in sql, f"{path.name} 缺少配置项 {key}"
    assert sql.count("'email'") >= len(EMAIL_CONFIG_KEYS), "所有项都必须是 email 分组"
    assert "is_secret" in sql


@pytest.mark.parametrize("path", [MYSQL_CFG, PG_CFG])
def test_smtp_password_is_secret_and_switch_defaults_off(path):
    sql = path.read_text(encoding="utf-8")
    # 密码行必须标记为密钥（平台首个 is_secret 使用者）
    pw_line = [l for l in sql.splitlines() if "email_smtp_password" in l]
    assert pw_line, "缺少密码配置项"
    assert any(("TRUE" in l or "1" in l) for l in pw_line), "密码必须 is_secret=1/TRUE"
    # 总开关默认关闭
    sw_line = [l for l in sql.splitlines() if "email_service_enabled" in l]
    assert sw_line and any("false" in l.lower() for l in sw_line), "总开关默认必须是 false"


def test_mysql_config_seed_is_idempotent():
    sql = MYSQL_CFG.read_text(encoding="utf-8")
    assert "INSERT IGNORE" in sql
    assert "UPDATE" not in sql.upper().replace("UPDATED_AT", "")


def test_pg_config_seed_is_idempotent():
    sql = PG_CFG.read_text(encoding="utf-8")
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_migration_contract.py -q`
Expected: FAIL

- [x] **Step 3: 写 MySQL 种子**

`db-prod/V164-add-email-service-configs.sql`：

```sql
-- V164: 系统配置新增「邮件服务」分组
-- 用途：全平台统一的 SMTP 发送服务，供审核结果通知与用户通知渠道的「使用全局」选项使用
-- 说明：总开关默认关闭；email_smtp_password 是平台首个 is_secret=1 配置，
--       仅在 API 出参脱敏，数据库内仍为明文（与既有用户级 SMTP 配置一致）。

INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('email_service_enabled', 'false', '是否启用平台邮件发送服务（关闭时不发送任何邮件，也不会阻塞审核等主流程）', 'email', 0),
('email_smtp_host', '', 'SMTP 服务器地址，例如 smtp.example.com 或内网中继 smtp.internal', 'email', 0),
('email_smtp_port', '465', 'SMTP 端口。SSL 常用 465，STARTTLS 常用 587，免认证内网中继常用 25', 'email', 0),
('email_smtp_security', 'ssl', '加密方式：ssl（SSL/TLS 直连）/ starttls（先明文连接再升级）/ none（不加密，仅适用于内网中继，不安全）', 'email', 0),
('email_smtp_user', '', 'SMTP 登录账号。免认证的内网中继请留空（此时密码也必须留空）', 'email', 0),
('email_smtp_password', '', 'SMTP 登录密码或授权码。免认证的内网中继请留空', 'email', 1),
('email_from_address', '', '发件人地址，留空则使用 SMTP 登录账号', 'email', 0),
('email_sender_name', 'NanZi AI Agent', '发件人显示名称，同时作为邮件主题前缀', 'email', 0);
```

- [x] **Step 4: 写 PG 种子**

`db-prod-pg/V65-add-email-service-configs.sql`：

```sql
-- V65: 系统配置新增「邮件服务」分组（对应 MySQL V164）

INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret") VALUES
('email_service_enabled', 'false', '是否启用平台邮件发送服务（关闭时不发送任何邮件，也不会阻塞审核等主流程）', 'email', FALSE),
('email_smtp_host', '', 'SMTP 服务器地址，例如 smtp.example.com 或内网中继 smtp.internal', 'email', FALSE),
('email_smtp_port', '465', 'SMTP 端口。SSL 常用 465，STARTTLS 常用 587，免认证内网中继常用 25', 'email', FALSE),
('email_smtp_security', 'ssl', '加密方式：ssl / starttls / none（none 不加密，仅适用于内网中继，不安全）', 'email', FALSE),
('email_smtp_user', '', 'SMTP 登录账号。免认证的内网中继请留空（此时密码也必须留空）', 'email', FALSE),
('email_smtp_password', '', 'SMTP 登录密码或授权码。免认证的内网中继请留空', 'email', TRUE),
('email_from_address', '', '发件人地址，留空则使用 SMTP 登录账号', 'email', FALSE),
('email_sender_name', 'NanZi AI Agent', '发件人显示名称，同时作为邮件主题前缀', 'email', FALSE)
ON CONFLICT ("key") DO NOTHING;
```

- [x] **Step 5: 加后端配置校验**

`app/services/config_service.py`：在常量区加 key 定义，并在 `validate_config_update` 里加对应分支（**参考现有 `USER_REGISTRATION_ENABLED_KEY` 分支的写法**）：

```python
    EMAIL_SERVICE_ENABLED_KEY = "email_service_enabled"
    EMAIL_SMTP_PORT_KEY = "email_smtp_port"
    EMAIL_SMTP_SECURITY_KEY = "email_smtp_security"
    EMAIL_FROM_ADDRESS_KEY = "email_from_address"
    EMAIL_SMTP_SECURITY_VALUES = ("ssl", "starttls", "none")
```

校验分支：

```python
    if key == ConfigService.EMAIL_SERVICE_ENABLED_KEY:
        if value.strip().lower() not in ConfigService._BOOLEAN_LITERALS:
            raise ValueError("邮件服务开关必须是布尔值")

    elif key == ConfigService.EMAIL_SMTP_PORT_KEY:
        # 允许空串（尚未填写），非空时必须是合法端口
        if value.strip():
            try:
                port = int(value.strip())
            except (TypeError, ValueError):
                raise ValueError("SMTP 端口必须是整数")
            if not (1 <= port <= 65535):
                raise ValueError("SMTP 端口必须在 1-65535 之间")

    elif key == ConfigService.EMAIL_SMTP_SECURITY_KEY:
        if value.strip().lower() not in ConfigService.EMAIL_SMTP_SECURITY_VALUES:
            raise ValueError("加密方式必须是 ssl、starttls 或 none")

    elif key == ConfigService.EMAIL_FROM_ADDRESS_KEY:
        # 允许空串；非空时必须是像样的邮箱。这里不 import AuthService，
        # 避免 config_service 反向依赖 auth_service（存在循环 import 风险）。
        text = value.strip()
        if text and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
            raise ValueError("发件人地址格式不正确")
```

确认 `config_service.py` 顶部已 `import re`。**刻意不校验「开启时必须填全」**：管理员常需分步填写，配置不完整时由运行期给出明确错误，并由「测试发送」按钮引导补齐。

- [x] **Step 6: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_user_email_migration_contract.py -q`
Expected: PASS

- [x] **Step 7: 补配置校验的单元测试**

> `validate_config_update` 是 `app/services/config_service.py:80` 的**模块级函数**，不是 `ConfigService` 类方法（早期计划版本写成 `ConfigService.validate_config_update(...)` 会 AttributeError）。直接从模块导入即可。
> 同理，Task 8 Step 5 的常量也放模块级（与既有 `USER_REGISTRATION_ENABLED_KEY` 同风格），不要挂到 `ConfigService` 类上。

```python
@pytest.mark.no_infrastructure
def test_email_config_validation_rules():
    from app.services.config_service import ConfigService

    validate_config_update("email_service_enabled", "true")
    validate_config_update("email_smtp_port", "587")
    validate_config_update("email_smtp_port", "")      # 允许未填写
    validate_config_update("email_smtp_security", "none")
    validate_config_update("email_from_address", "")   # 允许未填写
    validate_config_update("email_from_address", "a@b.cn")

    for key, bad in [
        ("email_service_enabled", "maybe"),
        ("email_smtp_port", "70000"),
        ("email_smtp_port", "abc"),
        ("email_smtp_security", "tls"),
        ("email_from_address", "not-an-email"),
    ]:
        with pytest.raises(ValueError):
            validate_config_update(key, bad)
```

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k email_config_validation`
Expected: PASS

## Task 9: 邮件发送服务

**Files:**
- Create: `app/services/email_delivery_service.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings


@pytest.mark.no_infrastructure
def test_smtp_settings_completeness_rule():
    # host 是唯一必需项：内部中继免认证，user/password 允许同时为空
    assert SmtpSettings(host="smtp.a.com", port=25, user="", password="",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is True
    assert SmtpSettings(host="", port=25, user="", password="",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is False
    # 只填了 user 或只填了 password：半套凭据几乎总是配置失误，必须判为不完整
    assert SmtpSettings(host="h", port=25, user="u", password="",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is False
    assert SmtpSettings(host="h", port=25, user="", password="p",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is False


@pytest.mark.no_infrastructure
def test_smtp_settings_effective_from_falls_back_to_user():
    s = SmtpSettings(host="h", port=25, user="login@a.com", password="",
                     security="none", from_address="", sender_name="X")
    assert s.effective_from() == "login@a.com"
    s2 = SmtpSettings(host="h", port=25, user="login@a.com", password="",
                      security="none", from_address="from@a.com", sender_name="X")
    assert s2.effective_from() == "from@a.com"


@pytest.mark.no_infrastructure
def test_parse_recipients_dedupes_and_strips():
    got = EmailDeliveryService.parse_recipients("a@x.com, B@Y.com ; a@x.com ,,")
    assert got == ["a@x.com", "B@Y.com"]


@pytest.mark.no_infrastructure
def test_compose_recipients_includes_self_by_default():
    got = EmailDeliveryService.compose_recipients(
        self_email="me@x.com", include_self=True, extra="other@x.com"
    )
    assert got == ["me@x.com", "other@x.com"]


@pytest.mark.no_infrastructure
def test_compose_recipients_dedupes_self_when_already_in_extra():
    """存量用户 recipients 里已经写了自己时不能重复收两封。"""
    got = EmailDeliveryService.compose_recipients(
        self_email="me@x.com", include_self=True, extra="me@x.com, other@x.com"
    )
    assert got == ["me@x.com", "other@x.com"]


@pytest.mark.no_infrastructure
def test_compose_recipients_without_self_or_email():
    assert EmailDeliveryService.compose_recipients(
        self_email=None, include_self=True, extra="other@x.com"
    ) == ["other@x.com"]
    assert EmailDeliveryService.compose_recipients(
        self_email="me@x.com", include_self=False, extra=""
    ) == []


@pytest.mark.no_infrastructure
def test_resolve_smtp_source_legacy_fallback():
    """存量兼容：缺 smtp_source 时按 smtp_host 是否已填自适应。"""
    assert EmailDeliveryService.resolve_smtp_source({"smtp_host": "smtp.a.com"}) == "custom"
    assert EmailDeliveryService.resolve_smtp_source({"smtp_host": ""}) == "global"
    assert EmailDeliveryService.resolve_smtp_source({}) == "global"
    assert EmailDeliveryService.resolve_smtp_source({"smtp_source": "global",
                                                     "smtp_host": "smtp.a.com"}) == "global"
    assert EmailDeliveryService.resolve_smtp_source({"smtp_source": "custom"}) == "custom"


@pytest.mark.no_infrastructure
def test_sanitize_error_text_never_leaks_password(monkeypatch):
    leaked = "auth failed for user u with password Sup3rSecret!"
    cleaned = EmailDeliveryService.sanitize_error_text(leaked, "Sup3rSecret!")
    assert "Sup3rSecret" not in cleaned
    assert "u" in cleaned
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k "smtp or recipients or smtp_source or sanitize"`
Expected: FAIL（模块不存在）

- [x] **Step 3: 实现服务**

新建 `app/services/email_delivery_service.py`：

```python
"""统一的邮件发送服务。

三件事：
1. 从系统配置加载全局 SMTP，或从用户通知配置加载自定义 SMTP；
2. 按调用方要求的分层（global / user）解析出最终 SMTP 设置；
3. 发信，并把底层错误整理成可直接展示给管理员的文本（绝不带密码）。

为什么要有这个模块：邮件发送原先只存在于 notification_service 的用户通知路径里，
而「审核结果通知」是系统发给用户的，必须走全局配置——不能因为用户把自己的 SMTP
关掉或填错就发不出去，那恰恰是他能否登录的前提。
"""
from __future__ import annotations

import logging
import re
import smtplib
from dataclasses import dataclass
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SmtpScope = Literal["user", "global"]

DEFAULT_TIMEOUT = 10


@dataclass
class SmtpSettings:
    host: str
    port: int
    user: str
    password: str
    security: str          # ssl | starttls | none
    from_address: str
    sender_name: str
    timeout: int = DEFAULT_TIMEOUT

    def is_complete(self) -> bool:
        """host 是唯一必需项。

        免认证的内部中继（内网最常见的邮件出口）合法地没有账号密码，
        所以 user/password 必须允许同时为空；但只填其中一个属于半套凭据，
        几乎总是配置失误——早报错比默默用匿名发送好。
        """
        if not (self.host or "").strip():
            return False
        return bool((self.user or "").strip()) == bool((self.password or "").strip())

    def effective_from(self) -> str:
        return (self.from_address or "").strip() or (self.user or "").strip()


class EmailDeliveryService:
    MASK = "***"

    # ---------------- 收件人 ---------------- #

    @staticmethod
    def parse_recipients(raw: Any) -> List[str]:
        """逗号/分号分隔 → 去空白 → 去重（保持原顺序）。"""
        if not raw:
            return []
        seen: List[str] = []
        for part in re.split(r"[,;]", str(raw)):
            addr = part.strip()
            if addr and addr not in seen:
                seen.append(addr)
        return seen

    @classmethod
    def compose_recipients(
        cls, self_email: Optional[str], include_self: bool, extra: Any
    ) -> List[str]:
        """「默认抄送自己」的真正落地点。

        include_self 存的是布尔标志而不是邮箱快照：用户之后改了邮箱，
        收件人必须自动跟着变。写快照会导致通知一直发往已废弃的地址。
        去重是为了兼容存量用户——他们的 recipients 里可能已经写了自己。
        """
        result: List[str] = []
        if include_self and self_email:
            result.append(self_email.strip())
        for addr in cls.parse_recipients(extra):
            if addr not in result:
                result.append(addr)
        return result

    # ---------------- 分层 ---------------- #

    @staticmethod
    def resolve_smtp_source(config: Dict[str, Any]) -> str:
        """返回 'global' 或 'custom'。

        存量兼容零迁移：老的 config_json 里没有 smtp_source，此时按 smtp_host
        是否已填自适应——填了就当自定义（行为与升级前完全一致），没填就用全局。
        自适应、可逆、不需要改历史数据。
        """
        source = str(config.get("smtp_source") or "").strip().lower()
        if source in ("global", "custom"):
            return source
        return "custom" if str(config.get("smtp_host") or "").strip() else "global"

    @staticmethod
    def _from_global_row(row: Dict[str, Any]) -> SmtpSettings:
        def _int(v, default):
            try:
                return int(str(v).strip())
            except (TypeError, ValueError):
                return default

        return SmtpSettings(
            host=str(row.get("email_smtp_host") or "").strip(),
            port=_int(row.get("email_smtp_port"), 465),
            user=str(row.get("email_smtp_user") or "").strip(),
            password=str(row.get("email_smtp_password") or ""),
            security=str(row.get("email_smtp_security") or "ssl").strip().lower(),
            from_address=str(row.get("email_from_address") or "").strip(),
            sender_name=str(row.get("email_sender_name") or "NanZi AI Agent").strip(),
        )

    @classmethod
    async def load_global_smtp_settings(cls) -> Optional[SmtpSettings]:
        """全局 SMTP；开关关闭或配置不完整返回 None。"""
        from app.services.config_service import ConfigService

        try:
            configs = await ConfigService.get_all_from_db()
        except Exception as exc:
            logger.warning("读取邮件服务配置失败: %s", exc)
            return None

        if str(configs.get("email_service_enabled", {}).get("value", "false")).strip().lower() not in (
            "true", "1", "yes", "on"
        ):
            return None

        row = {k: v.get("value") for k, v in configs.items() if k.startswith("email_")}
        settings = cls._from_global_row(row)
        if not settings.is_complete():
            return None
        return settings

    @classmethod
    async def load_user_smtp_settings(
        cls, user_id: int, db: Optional[AsyncSession] = None
    ) -> Tuple[Optional[SmtpSettings], str]:
        """返回 (设置, 实际来源)。设置可能为 None（配置不完整或未启用）。"""
        from app.services.notification_service import NotificationService

        own_session = db is None
        session = db
        if own_session:
            from app.core.orm import AsyncSessionLocal
            session = AsyncSessionLocal()
        try:
            record = await NotificationService.get_config_by_type_raw(session, user_id, "email")
            config = {}
            if record and record.config_json:
                import json
                try:
                    config = json.loads(record.config_json)
                except Exception:
                    config = {}

            source = cls.resolve_smtp_source(config)
            if source == "global":
                return await cls.load_global_smtp_settings(), "global"

            settings = SmtpSettings(
                host=str(config.get("smtp_host") or "").strip(),
                port=int(config.get("smtp_port") or 465),
                user=str(config.get("smtp_user") or "").strip(),
                password=str(config.get("smtp_password") or ""),
                security=str(config.get("security") or config.get("smtp_security") or "ssl").strip().lower(),
                from_address=str(config.get("from_address") or "").strip(),
                sender_name=str(config.get("sender_name") or "NanZi AI Agent").strip(),
            )
            return (settings if settings.is_complete() else None), "custom"
        finally:
            if own_session and session is not None:
                await session.close()

    @classmethod
    async def resolve_smtp_settings(
        cls, user_id: Optional[int], scope: SmtpScope = "user", db: Optional[AsyncSession] = None
    ) -> Optional[SmtpSettings]:
        """scope='global' 时**强制**用全局配置，忽略用户个人设置。

        审核结果通知走 global：那是系统发给用户的，不能因为用户把个人 SMTP
        关掉或填错就发不出去。
        """
        if scope == "global" or user_id is None:
            return await cls.load_global_smtp_settings()
        settings, _ = await cls.load_user_smtp_settings(user_id, db=db)
        return settings

    # ---------------- 发送 ---------------- #

    @staticmethod
    def sanitize_error_text(text: str, password: str) -> str:
        """错误文本要回传给管理员，因此必须先抹掉密码。

        SMTP 的认证失败信息里可能带上尝试使用的口令，直接透传等于在界面上泄露凭据。
        """
        cleaned = str(text or "")
        if password:
            cleaned = cleaned.replace(password, EmailDeliveryService.MASK)
        return cleaned

    @classmethod
    def send_mail(
        cls, settings: SmtpSettings, to: List[str], subject: str, body: str
    ) -> Tuple[bool, str]:
        """同步发送。返回 (ok, message)。不抛异常，调用方不需要 try。"""
        if not settings.is_complete():
            return False, "邮件服务配置不完整（至少需要填写 SMTP 服务器地址）"
        if not to:
            return False, "未配置收件人"

        sender = settings.effective_from()
        if not sender:
            return False, "未配置发件人地址（发件人地址与 SMTP 登录账号都为空）"

        message = MIMEText(body, "plain", "utf-8")
        message["Subject"] = Header(subject, "utf-8")
        message["From"] = formataddr((str(Header(settings.sender_name, "utf-8")), sender))
        message["To"] = ", ".join(to)

        try:
            if settings.security == "ssl":
                server = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout)
            else:
                server = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)

            with server:
                server.ehlo()
                if settings.security == "starttls":
                    # 绝不静默降级：既有实现把 starttls 失败吞掉，导致 587 端口可能
                    # 明文发送凭据。这里失败就报错，让管理员看到真实原因。
                    server.starttls()
                    server.ehlo()
                if settings.user:
                    server.login(settings.user, settings.password)
                server.sendmail(sender, to, message.as_string())
            return True, "发送成功"
        except Exception as exc:
            return False, cls.sanitize_error_text(f"{type(exc).__name__}: {exc}", settings.password)
```

> 注：`load_user_smtp_settings` 依赖 `NotificationService.get_config_by_type_raw`，该方法已存在（`notification_service.py:141`）。若其签名不同，用 `grep -n "def get_config_by_type_raw" -A 12 app/services/notification_service.py` 核对后对齐。

- [x] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k "smtp or recipients or smtp_source or sanitize"`
Expected: PASS

## Task 10: 测试发送端点

**Files:**
- Modify: `app/api/portal/endpoints/system.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
EMAIL_TEST_URL = "/api/portal/system/configs/email/test"


async def test_email_test_endpoint_requires_permission(client, valid_api_key):
    """普通用户没有 element:system:config_save，必须被拒。"""
    resp = await client.post(EMAIL_TEST_URL, json={}, headers={"X-API-Key": valid_api_key})
    assert resp.status_code in (401, 403)


async def test_email_test_endpoint_reports_disabled_service(client, admin_api_key):
    resp = await client.post(EMAIL_TEST_URL, json={}, headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "未启用" in body["message"] or "配置不完整" in body["message"]


async def test_email_test_endpoint_never_leaks_password(client, admin_api_key, monkeypatch):
    """错误文本里出现密码时必须在回传前抹掉。"""
    from app.services import email_delivery_service as mod

    settings = mod.SmtpSettings(host="bad.invalid", port=25, user="u",
                                password="Sup3rSecret!", security="none",
                                from_address="f@a.cn", sender_name="X")
    async def _fake_global():
        return settings
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _fake_global)

    resp = await client.post(EMAIL_TEST_URL, json={"to": "t@a.cn"},
                             headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    assert "Sup3rSecret" not in resp.text
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k email_test_endpoint`
Expected: FAIL（404）

- [x] **Step 3: 实现端点**

`app/api/portal/endpoints/system.py` 加：

```python
class EmailTestRequest(BaseModel):
    to: Optional[str] = None


@router.post("/configs/email/test", summary="测试邮件服务配置")
async def test_email_service(
    payload: EmailTestRequest,
    user: Dict = Depends(require_permission("element", "element:system:config_save")),
):
    """用**已保存**的配置发一封测试邮件。

    刻意不接受请求体里的 SMTP 参数：前端拿到的是脱敏后的密码，无法用它发信；
    因此这里只认库里已保存的值，界面必须提示「请先保存再测试」。
    """
    from app.services.email_delivery_service import EmailDeliveryService

    settings = await EmailDeliveryService.load_global_smtp_settings()
    if settings is None:
        return {"ok": False, "message": "邮件服务未启用，或配置不完整（至少需要 SMTP 服务器地址）"}

    to = EmailDeliveryService.parse_recipients(payload.to)
    if not to:
        fallback = settings.effective_from()
        if not fallback:
            return {"ok": False, "message": "未指定收件人，且未配置发件人地址可用于自测"}
        to = [fallback]

    ok, message = EmailDeliveryService.send_mail(
        settings, to, f"【{settings.sender_name}】邮件服务测试",
        "这是一封测试邮件。收到它说明平台邮件服务配置可用。\n",
    )
    return {"ok": ok, "message": message}
```

`send_mail` 是同步阻塞的，用 `await run_in_threadpool(...)` 包装以免阻塞事件循环：

```python
    from starlette.concurrency import run_in_threadpool
    ok, message = await run_in_threadpool(
        EmailDeliveryService.send_mail, settings, to, subject, body
    )
```

- [x] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k email_test_endpoint`
Expected: PASS

## Task 11: 前端「邮件服务」分组

**Files:**
- Modify: `frontend/src/views/SystemConfig.vue`
- Modify: `tests/frontend/test_user_email_contract.py`

- [x] **Step 1: 写前端契约测试**

```python
def test_email_category_registered_after_other():
    src = _read("frontend/src/views/SystemConfig.vue")
    # 顺序数组里 email 必须在 other 之后
    marker = "const order = ["
    start = src.index(marker)
    order_line = src[start:src.index("]", start)]
    assert "'email'" in order_line, "分组顺序数组必须包含 email"
    assert order_line.index("'email'") > order_line.index("'other'"), "邮件服务必须在其他参数之后"


def test_email_category_has_label_icon_and_subtitle():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "邮件服务" in src
    for marker in ("const map: Record<string, { short: string; full: string }>",
                   "const CATEGORY_ICONS",
                   "const getGroupSubtitle"):
        assert marker in src


def test_email_boolean_key_registered_in_toggle_list():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "'email_service_enabled'" in src, "布尔开关必须登记到硬编码清单，否则退化成文本框"


def test_email_security_key_registered_as_select():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "'email_smtp_security'" in src


def test_email_group_has_test_button():
    src = _read("frontend/src/views/SystemConfig.vue")
    assert "configs/email/test" in src, "必须有测试发送按钮"
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: 新增的 5 个用例 FAIL

- [x] **Step 3: 注册分组（4 处）**

`frontend/src/views/SystemConfig.vue`：

1. `orderedCategories` 的 `order` 数组末尾加 `'email'`（在 `'other'` 之后）
2. `getCategoryLabel` 的 map 加：
   ```js
   'email':         { short: '邮件服务',    full: '邮件服务 (Mail Service)' },
   ```
3. `CATEGORY_ICONS` 加 `'email': EnvelopeIcon`（并从 `@heroicons/vue/24/outline` 引入 `EnvelopeIcon`）
4. `getGroupSubtitle` 加 email 分支：`'配置平台统一的邮件发送服务（SMTP），用于审核结果通知等系统邮件'`

- [x] **Step 4: 登记渲染清单**

1. 布尔开关硬编码数组（`includes(item.key)` 那个数组）加 `'email_service_enabled'`
2. `email_smtp_security` 需要下拉渲染：找到现有下拉渲染的 key 清单分支（`<select v-model="item.value"` 附近），加一个 email_smtp_security 分支，选项为 `ssl`（SSL/TLS，常用 465）/ `starttls`（STARTTLS，常用 587）/ `none`（不加密，仅内网中继，不安全）
3. `email_smtp_port` 需要数字输入：参考现有整数型配置（如「30 分钟」那个分支）加一个数字输入分支
4. `email_smtp_password` 无需处理：后端返回 `is_secret=true`，前端已按 `item.is_secret` 渲染密码框 + 显隐切换

- [x] **Step 5: 加测试发送按钮**

在「邮件服务」分组的卡片内加一个按钮（仅 `category === 'email'` 时显示），点击调：

```ts
const testEmailService = async () => {
  emailTestSending.value = true
  try {
    const to = window.prompt('测试收件人（留空则发给发件人地址）') || ''
    const { data } = await axios.post('/api/portal/system/configs/email/test', { to })
    showToast(data.message, data.ok ? 'success' : 'error')
  } catch (e: any) {
    showToast(`测试失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    emailTestSending.value = false
  }
}
```

按钮文案「发送测试邮件」，并提示「使用已保存的配置，请先保存再测试」。

- [x] **Step 6: 运行契约测试 + 类型检查**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: PASS

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: 零新增错误

---

# 阶段 C：审核结果邮件

## Task 12: 审核邮件文案

**Files:**
- Modify: `app/services/email_delivery_service.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
@pytest.mark.no_infrastructure
def test_review_mail_subject_and_body_reflect_the_outcome():
    from app.services.email_delivery_service import build_review_mail

    subject, body = build_review_mail("zhangsan", approved=True, sender_name="NanZi")
    assert "启用" in subject and "NanZi" in subject
    assert "zhangsan" in body
    assert "通过审核" in body
    assert "管理员" in body, "要给出下一步求助路径，否则用户被卡住无从下手"

    subject2, body2 = build_review_mail("zhangsan", approved=False, sender_name="NanZi")
    assert "未通过" in subject2
    assert "zhangsan" in body2
    assert "管理员" in body2


@pytest.mark.no_infrastructure
def test_review_mail_cannot_leak_credentials():
    """审核邮件不得泄露凭据——从**来源**上保证，而不是靠禁用某个词。

    早先这里的断言是 `assert "密码" not in body`，它禁掉的是「密码」这个**词**：
    结果连「如忘记密码，请联系管理员重置」这种正当提示也被禁了，逼着文案改用
    「登录口令」去绕开断言——测试反而在推着实现变差。

    真正要守的性质是：这个函数**根本拿不到**任何凭据。所以断言它的入参只有
    账号名、审核结果、发件人名称。将来若有人为了「顺手告知密码」而加一个
    password 参数，这条断言会立刻失败。
    """
    import inspect

    from app.services.email_delivery_service import build_review_mail

    params = list(inspect.signature(build_review_mail).parameters)
    assert params == ["user_name", "approved", "sender_name"], (
        f"审核邮件文案函数的入参被改动了（{params}）：它必须拿不到任何凭据，"
        "否则正文就可能泄露口令"
    )
```

> ⚠️ 早期版本这里既有恒真断言 `assert "zheng" not in subject or True`，又有与本节正文
> 文案自相矛盾的 `assert "密码" not in body`（正文本来就写了「如忘记密码，请联系管理员重置」）。
> 两者都已修正：恒真断言删除，词汇禁令改为上面的入参契约断言。

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k review_mail`
Expected: FAIL

- [x] **Step 3: 实现文案**

加到 `app/services/email_delivery_service.py`：

```python
def build_review_mail(user_name: str, approved: bool, sender_name: str) -> Tuple[str, str]:
    """审核结果邮件文案。

    刻意不写登录地址：平台没有「站点基础地址」配置项，硬编码会写死部署形态。
    也刻意不提密码——注册密码是用户自己设的，平台无法也不该声称能告知。
    """
    if approved:
        subject = f"【{sender_name}】您的账号已启用"
        body = (
            f"您好，{user_name}：\n\n"
            f"您的账号申请已通过审核，现在可以登录平台了。\n\n"
            f"请使用注册时设置的账号名与密码登录。如忘记密码，请联系管理员重置。\n\n"
            f"（本邮件由系统自动发送，请勿直接回复）\n"
        )
    else:
        subject = f"【{sender_name}】您的账号申请未通过"
        body = (
            f"您好，{user_name}：\n\n"
            f"很抱歉，您的账号申请未通过审核，当前无法登录平台。\n\n"
            f"如有疑问，请联系平台管理员了解具体原因。\n\n"
            f"（本邮件由系统自动发送，请勿直接回复）\n"
        )
    return subject, body
```

- [x] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k review_mail`
Expected: PASS

## Task 13: 审核动作触发发信

**Files:**
- Modify: `app/api/portal/endpoints/management.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
STATUS_URL = "/api/portal/management/users/{uid}/status"


async def test_review_approval_sends_mail(client, admin_api_key, db_session, monkeypatch):
    sent = {}
    from app.services import email_delivery_service as mod

    async def _fake_settings(user_id=None, scope="user", db=None):
        return mod.SmtpSettings(host="h", port=25, user="", password="",
                                security="none", from_address="f@a.cn", sender_name="X")
    def _fake_send(settings, to, subject, body):
        sent["to"] = to; sent["subject"] = subject
        return True, "发送成功"
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _fake_settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_fake_send))

    name, email = await _seed_pending(f"zrev_{uuid.uuid4().hex[:8]}",
                                     f"rev_{uuid.uuid4().hex[:8]}@corp.example.com")
    resp = await client.patch(STATUS_URL.format(uid=name[0]), json={"status": 1},
                              headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("email_sent") is True
    assert sent["to"] == [email]
    await _drop_user(name[1])


async def test_review_does_not_send_when_service_disabled(
    client, admin_api_key, db_session, monkeypatch
):
    from app.services import email_delivery_service as mod

    async def _none(user_id=None, scope="user", db=None):
        return None
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _none)

    uid, name = await _seed_pending(f"zrev_{uuid.uuid4().hex[:8]}",
                                   f"rev_{uuid.uuid4().hex[:8]}@corp.example.com")
    resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                              headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200, "邮件服务关闭绝不能阻塞审核"
    assert resp.json().get("email_sent") is False
    await _drop_user(name)


async def test_review_records_error_when_send_fails(client, admin_api_key, monkeypatch):
    from app.services import email_delivery_service as mod

    async def _settings(user_id=None, scope="user", db=None):
        return mod.SmtpSettings(host="h", port=25, user="", password="",
                                security="none", from_address="f@a.cn", sender_name="X")
    def _fail(settings, to, subject, body):
        return False, "SMTPAuthenticationError: 认证失败"
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_fail))

    uid, name = await _seed_pending(f"zrev_{uuid.uuid4().hex[:8]}",
                                   f"rev_{uuid.uuid4().hex[:8]}@corp.example.com")
    resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                              headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    assert resp.json()["email_sent"] is False
    assert "认证失败" in resp.json()["email_error"]
    await _drop_user(name)


async def test_review_skips_mail_when_user_has_no_email(
    client, admin_api_key, monkeypatch
):
    from app.services import email_delivery_service as mod

    async def _settings(user_id=None, scope="user", db=None):
        return mod.SmtpSettings(host="h", port=25, user="", password="",
                                security="none", from_address="f@a.cn", sender_name="X")
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)

    uid, name = await _seed_pending(f"zrev_{uuid.uuid4().hex[:8]}", None)
    resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                              headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    assert resp.json()["email_sent"] is False
    assert "邮箱" in resp.json()["email_error"]
    await _drop_user(name)


async def test_review_does_not_mail_on_daily_enable_disable(
    client, admin_api_key, monkeypatch
):
    """原状态不是待审核的启停属于日常运维，发信会变成骚扰。"""
    from app.services import email_delivery_service as mod
    called = {"n": 0}
    async def _settings(user_id=None, scope="user", db=None):
        return mod.SmtpSettings(host="h", port=25, user="", password="",
                                security="none", from_address="f@a.cn", sender_name="X")
    def _send(settings, to, subject, body):
        called["n"] += 1
        return True, "ok"
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_send))

    uid, name = await _seed_enabled(f"zdaily_{uuid.uuid4().hex[:8]}",
                                    f"daily_{uuid.uuid4().hex[:8]}@corp.example.com")
    await client.patch(STATUS_URL.format(uid=uid), json={"status": 0},
                       headers={"X-API-Key": admin_api_key})
    await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                       headers={"X-API-Key": admin_api_key})
    assert called["n"] == 0, "非审核动作不得发信"
    await _drop_user(name)
```

配套助手 `_seed_pending` / `_seed_enabled`（建号后返回 `(id, user_name)`）。

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k review`
Expected: FAIL（响应里没有 `email_sent`）

- [x] **Step 3: 实现发信**

`management.py` 的 `update_user_status` 中，**在把 `status` 写入之前**先记下原状态与邮箱：

```python
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 记录审核前的状态：只有「待审核 → 启用/禁用」才是审核动作。
    # 用它区分审核与日常启停，避免管理员事后启停账号时给用户发骚扰邮件，
    # 而且不需要额外字段或动作类型参数。
    was_pending = target.status == USER_STATUS_PENDING_REVIEW
    target_email = target.email
```

在 commit 成功之后追加：

```python
    # 审核结果通知：尽力而为，绝不影响审核结果本身。
    email_sent = False
    email_error: Optional[str] = None
    if was_pending and target.status in (USER_STATUS_ENABLED, USER_STATUS_DISABLED):
        email_sent, email_error = await _send_review_result_mail(
            target, approved=(target.status == USER_STATUS_ENABLED)
        )
```

并把返回值**只新增**两个字段：

```python
    # 只新增 email_sent / email_error，**不改动**既有的 message 取值。
    # （实测既有实现返回的是 {"message": "User status updated successfully"}，
    #  既没有 status 字段、message 也是英文——早期计划版本凭空改写了这两个值，
    #  与本节验收点「只新增」相矛盾，已修正。）
    return {
        "message": "User status updated successfully",   # 保持既有原值不变
        "email_sent": email_sent,
        "email_error": email_error,
    }
```

> 另注：这个函数里注入的 ORM 对象变量名是 `user`（不是片段里的 `target`）。发信调用要放在
> `commit` **之后**、且在所有可能抛错的动作（业务角色分配、缓存清理）**之后**——
> 否则角色分配失败抛 500 时，用户却已经收到了「审核已通过」的邮件。

在 `management.py` 内加模块级私有函数：

```python
async def _send_review_result_mail(user: User, approved: bool):
    """审核结果通知，返回 (sent, error)。

    强制走**全局** SMTP（已决策）：这是系统发给用户的通知，若走用户个人配置，
    用户把个人 SMTP 关掉或填错就永远收不到审核结果——而这恰恰是他能否登录的前提。
    """
    from starlette.concurrency import run_in_threadpool

    from app.services.email_delivery_service import (
        EmailDeliveryService, build_review_mail,
    )

    if not (user.email or "").strip():
        return False, "该用户未填写邮箱"

    settings = await EmailDeliveryService.load_global_smtp_settings()
    if settings is None:
        return False, "平台邮件服务未启用或配置不完整"

    subject, body = build_review_mail(user.user_name, approved, settings.sender_name)
    ok, message = await run_in_threadpool(
        EmailDeliveryService.send_mail, settings, [user.email], subject, body
    )
    if not ok:
        logger.warning("审核结果邮件发送失败: user=%s reason=%s", user.user_name, message)
    return ok, (None if ok else message)
```

`send_mail` 是阻塞的，`run_in_threadpool` 包装避免卡住事件循环。

- [x] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k review`
Expected: PASS

- [x] **Step 5: 回归既有审核测试**

Run: `.venv/bin/python -m pytest tests/test_user_registration.py -q`
Expected: 79 passed（响应新增字段不得破坏既有断言）

## Task 14: 重发端点

**Files:**
- Modify: `app/api/portal/endpoints/management.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
RESEND_URL = "/api/portal/management/users/{uid}/notify-review-mail"


async def test_resend_review_mail(client, admin_api_key, monkeypatch):
    from app.services import email_delivery_service as mod
    captured = {}
    async def _settings(user_id=None, scope="user", db=None):
        return mod.SmtpSettings(host="h", port=25, user="", password="",
                                security="none", from_address="f@a.cn", sender_name="X")
    def _send(settings, to, subject, body):
        captured["subject"] = subject
        return True, "ok"
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_send))

    uid, name = await _seed_enabled(f"zres_{uuid.uuid4().hex[:8]}",
                                    f"res_{uuid.uuid4().hex[:8]}@corp.example.com")
    resp = await client.post(RESEND_URL.format(uid=uid), json={"approved": True},
                             headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert "启用" in captured["subject"]
    await _drop_user(name)


async def test_resend_rejected_for_pending_user(client, admin_api_key):
    uid, name = await _seed_pending(f"zres_{uuid.uuid4().hex[:8]}", "p@a.cn")
    resp = await client.post(RESEND_URL.format(uid=uid), json={"approved": True},
                             headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 400
    await _drop_user(name)
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k resend`
Expected: FAIL（404）

- [x] **Step 3: 实现端点**

```python
class NotifyReviewMailRequest(BaseModel):
    # 显式传事件而不是从当前状态推断：重发往往发生在用户状态已被改动之后，
    # 从状态推断会发出与实际审核决定不符的文案。
    approved: bool


@router.post(
    "/users/{user_id}/notify-review-mail",
    dependencies=[Depends(require_permission("element", "element:user:edit"))],
)
async def notify_review_mail(
    user_id: int,
    request: NotifyReviewMailRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """重发审核结果邮件。"""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.status == USER_STATUS_PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="该用户仍在待审核状态，无需重发通知")

    sent, error = await _send_review_result_mail(target, approved=request.approved)
    return {"ok": sent, "message": "已发送" if sent else (error or "发送失败")}
```

- [x] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k resend`
Expected: PASS

## Task 15: 前端审核发信反馈与重发按钮

**Files:**
- Modify: `frontend/src/views/Users.vue`
- Modify: `tests/frontend/test_user_email_contract.py`

- [x] **Step 1: 写契约测试**

```python
def test_users_view_reports_mail_result_and_offers_resend():
    src = _read("frontend/src/views/Users.vue")
    assert "email_sent" in src, "审核后必须区分通知邮件是否发送成功"
    assert "notify-review-mail" in src, "必须有重发入口"
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: 新增 1 个用例 FAIL

- [x] **Step 3: 改造审核反馈**

`Users.vue` 的 `approveUser` / `rejectUser` 里，读取响应中的 `email_sent` / `email_error`：

- 成功且 `email_sent === true` → toast「已启用，通知邮件已发送」
- 成功但 `email_sent === false` → toast 警告「已启用，但通知邮件未发送：{email_error}」，并把该用户记为「最近一次发信失败」
- 在待审核卡片与用户列表的「更多」菜单里，对发信失败的用户显示「重发通知邮件」按钮，点击调：

```ts
const resendReviewMail = async (userId: number, approved: boolean) => {
  try {
    const { data } = await axios.post(
      `/api/portal/management/users/${userId}/notify-review-mail`, { approved }
    )
    showToast(data.message, data.ok ? 'success' : 'error')
  } catch (e: any) {
    showToast(`重发失败: ${e.response?.data?.detail || e.message}`, 'error')
  }
}
```

- [x] **Step 4: 运行契约测试 + 类型检查**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: PASS

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: 零新增错误

---

# 阶段 D：个人中心通知分层 + SSO

## Task 16: 通知渠道接入分层与「抄送自己」

**Files:**
- Modify: `app/services/notification_service.py`
- Modify: `app/services/ai/tools/notification_tools.py`
- Test: `tests/test_user_email_and_mail_service.py`

- [x] **Step 1: 写失败测试**

```python
@pytest.mark.no_infrastructure
def test_email_default_config_has_source_and_include_self():
    from app.services.notification_service import NotificationService
    cfg = NotificationService.DEFAULT_CONFIGS["email"]
    assert cfg["smtp_source"] == "global", "默认使用全局邮件服务"
    assert cfg["include_self"] is True, "默认抄送自己"
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k default_config`
Expected: FAIL

- [x] **Step 3: 扩展默认配置**

`notification_service.py` 的 `DEFAULT_CONFIGS["email"]` 加两个键：

```python
        "smtp_source": "global",   # global=用平台统一配置；custom=用下面这套自定义 SMTP
        "include_self": True,      # 是否同时发送到用户自己的邮箱（存标志而非邮箱快照，
                                   # 这样用户改邮箱后收件人自动跟随）
```

- [x] **Step 4: 改造 send_email**

`notification_service.py` 的 `send_email` 改为用分层解析与新的收件人拼接：

```python
    async def send_email(cls, db, user_id, title, content):
        """按用户选择的来源（全局/自定义）发信，收件人默认包含用户自己的邮箱。"""
        from app.models.user import User
        from app.services.email_delivery_service import EmailDeliveryService

        record = await cls.get_config_by_type_raw(db, user_id, "email")
        config = {}
        if record and record.config_json:
            try:
                config = json.loads(record.config_json)
            except Exception:
                config = {}

        if not config.get("is_enabled"):
            return False, "用户未启用邮件通知"

        settings = await EmailDeliveryService.resolve_smtp_settings(user_id, scope="user", db=db)
        if settings is None:
            return False, "邮件服务未配置（当前来源无可用的 SMTP 设置）"

        # 用户自己的邮箱
        self_email = (
            await db.execute(select(User.email).where(User.id == user_id))
        ).scalar()

        include_self = config.get("include_self", True)   # 缺失默认 True，新老用户一致
        recipients = EmailDeliveryService.compose_recipients(
            self_email=self_email, include_self=include_self, extra=config.get("recipients")
        )
        if not recipients:
            # 兼容既有行为：自定义 SMTP 下 recipients 为空时原先回退发给 smtp_user。
            # 全局模式下不回退——发给发件账号自己是静默的错误投递。
            if EmailDeliveryService.resolve_smtp_source(config) == "custom":
                fallback = settings.effective_from()
                recipients = [fallback] if fallback else []
        if not recipients:
            return False, "未配置收件人（未填写邮箱，且未设置其他收件人）"

        def send_sync():
            return EmailDeliveryService.send_mail(settings, recipients, title, content)

        return await run_in_threadpool(send_sync)
```

> 保留原函数既有的 `run_in_threadpool` 用法；若原实现直接用 `asyncio.to_thread`，沿用它。

- [x] **Step 5: 消除 notification_tools 的 STARTTLS 静默降级**

`notification_tools.py` 里手工构造 SMTP 的那段（`:157-165`）改为走统一服务，避免两份实现漂移：

```python
        from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings
        settings = SmtpSettings(
            host=str(config.get("smtp_host") or "").strip(),
            port=int(config.get("smtp_port") or 465),
            user=str(config.get("smtp_user") or "").strip(),
            password=str(config.get("smtp_password") or ""),
            security=str(config.get("security") or "ssl").strip().lower(),
            from_address=str(config.get("from_address") or "").strip(),
            sender_name=str(config.get("sender_name") or "AI Agent").strip(),
        )
        ok, message = EmailDeliveryService.send_mail(settings, [to_email], subject, body)
```

- [x] **Step 6: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py -q -k default_config`
Expected: PASS

Run: `.venv/bin/python -m pytest tests -q -k "notification"` （确认既有通知测试未退化）

## Task 17: 个人中心通知界面改造

**Files:**
- Modify: `frontend/src/components/personal/NotificationConfigs.vue`
- Modify: `frontend/src/api/portal.ts`
- Modify: `tests/frontend/test_user_email_contract.py`

- [x] **Step 1: 写契约测试**

```python
def test_notification_configs_has_source_radio_and_include_self():
    src = _read("frontend/src/components/personal/NotificationConfigs.vue")
    assert "smtp_source" in src, "必须有全局/自定义来源选择"
    assert "include_self" in src, "必须有抄送自己开关"
    assert "同时发送到我的邮箱" in src
    # 默认勾选
    assert "include_self: true" in src or "include_self ?? true" in src or "include_self !== false" in src


def test_notification_configs_hides_custom_smtp_when_global():
    src = _read("frontend/src/components/personal/NotificationConfigs.vue")
    assert "smtp_source === 'custom'" in src or "smtp_source === \"custom\"" in src
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: 新增 2 个用例 FAIL

- [x] **Step 3: 改造界面**

`NotificationConfigs.vue` 的邮件渠道区块：

1. 顶部加「邮件服务来源」单选（`v-model="configs.email.smtp_source"`）：
   - `global`：「使用全局邮件服务（推荐）」+ 副文案「由平台统一配置，无需在此填写服务器信息」
   - `custom`：「使用自定义 SMTP」
2. `v-if="configs.email.smtp_source === 'custom'"` 包裹原有 host/port/user/password/sender 字段；`global` 时改为显示全局概览（调 `GET /api/portal/system/configs` 拿 host 与发件人，**不显示密码**）；全局未启用或未配置时提示「平台尚未启用邮件服务，请联系管理员配置，或改用自定义 SMTP」
3. 收件人区改造：
   - 复选框 `v-model="configs.email.include_self"`，标签「同时发送到我的邮箱」+ 显示当前邮箱（`{{ me?.email || '未设置' }}`）
   - 邮箱为空时禁用复选框 + 提示「请先在基本信息中填写邮箱」
   - 保留下方「其他收件人」输入框（对应 `recipients`）
4. 读取配置时补默认值：`include_self: raw.include_self !== false`（缺失视为 true）、`smtp_source: raw.smtp_source || 'global'`
5. `frontend/src/api/portal.ts` 的通知配置类型加 `smtp_source?: 'global' | 'custom'` 与 `include_self?: boolean`

- [x] **Step 4: 运行契约测试 + 类型检查**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q`
Expected: PASS

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: 零新增错误

## Task 18: 邮箱参与第三方用户同步

**Files:**
- Modify: `app/schemas/user_sync.py`
- Modify: `app/services/user_sync_service.py`
- Modify: `frontend/src/components/ThirdPartyUserSyncDrawer.vue`
- Modify: `frontend/src/views/SystemConfig.vue`
- Test: `tests/frontend/test_user_email_contract.py`

- [x] **Step 1: 契约测试**

```python
def test_user_sync_field_map_includes_email():
    src = _read("app/schemas/user_sync.py")
    assert "email" in src


def test_user_sync_drawer_offers_email_mapping():
    src = _read("frontend/src/components/ThirdPartyUserSyncDrawer.vue")
    assert "email" in src
```

- [x] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q -k sync`
Expected: FAIL

- [x] **Step 3: 后端映射与落库**

- `app/schemas/user_sync.py` 的 `ThirdPartyUserSyncFieldMap` 加 `email: Optional[str] = None`
- `user_sync_service.py`：
  - 映射构造处（`:150` 附近）加 `"email": field_map.email,`
  - 行规范化处（`:283` 附近）加 `"email": (str(row.get("email")).strip() if row.get("email") is not None else None),`
  - **落库前必须归一化**：加
    ```python
            if ext_user.get("email"):
                ext_user["email"] = normalize_email(ext_user["email"])  # 小写 + 格式兜底
    ```
    非法或空值置 `None`，**并且**：若该邮箱已被其他用户占用，跳过写入并记 WARNING（不能让一个用户的同步失败阻塞整批）
  - 更新分支（`:351` 附近）加 `if ext_user.get("email"): existing.email = ext_user["email"]`，`None` 时不覆盖（避免第三方未提供该字段就清空用户手工填的邮箱）
  - 新建分支（`:361` 附近）加 `email=ext_user.get("email")`
- `SystemConfig.vue` 的第三方同步映射配置区加 email 输入项

- [x] **Step 4: 运行契约测试**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_user_email_contract.py -q -k sync`
Expected: PASS

## Task 19: 全量回归与清单同步

**Files:**
- Modify: `tests/CHECKLIST.md`

- [x] **Step 1: 后端全量**

Run: `.venv/bin/python -m pytest tests/test_user_email_and_mail_service.py tests/test_user_email_migration_contract.py tests/test_user_registration.py tests/test_user_registration_migration_contract.py -q`
Expected: 全绿

Run: `.venv/bin/python -m pytest tests/api tests/core -q`
Expected: 647 passed + 10 xfailed（不得退化）

- [x] **Step 2: 前端全量**

Run: `.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q`
Expected: 1342 passed + 1 skipped（加上本特性新增的用例）

- [x] **Step 3: 类型检查**

Run: `cd frontend && NODE_OPTIONS=--max-old-space-size=8192 npx vue-tsc --noEmit -p tsconfig.app.json > /tmp/tsc_after_email.txt 2>&1; diff /tmp/tsc_before.txt /tmp/tsc_after_email.txt | head -40`
Expected: 无差异（零新增错误）

- [x] **Step 4: 确认没有测试残留**

Run:
```bash
.venv/bin/python -c "
import asyncio
from sqlalchemy import select
from app.core.orm import AsyncSessionLocal
from app.models.user import User
async def m():
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(User.user_name).where(
            (User.status==2) | (User.user_name.like('z%'))))).scalars().all()
print('RESIDUE:', asyncio.run(m()))
"
```
Expected: `RESIDUE: []` —— 本特性的测试必须自清理，不得在共享开发库里留垃圾账号

- [x] **Step 5: 同步 CHECKLIST**

在 `tests/CHECKLIST.md` 表格顶部新增一行，列：特性名、涉及核心文件、方案设计与测试闭环说明（含迁移编号、8 个配置 key、分层语义、审核发信不阻塞、收件人用标志而非快照、`is_secret` 首次使用、SSO 邮箱覆盖手改的行为）、验收状态、交付日期。

- [x] **Step 6: 不提交**

按仓库约定，**不执行 `git commit`**，向用户汇报改动清单与验证结果。

---

## 自查记录（写计划时同步修掉的问题）

- **`IntegrityError` 区分**：原设计只说「要区分」，计划里落成了 `is_email_unique_violation()` 并配了专门的测试 `test_email_conflict_is_not_reported_as_user_name_conflict`。这是本特性最容易写错的一处。
- **编辑清空邮箱**：`update_user` 必须用「字段是否出现在请求里」判定，否则管理员无法清空邮箱。已在 Task 4 Step 5 显式标注。
- **5 处用户 dict**：漏一处就有鉴权路径拿不到 email，Task 5 用 `grep -c` 校验数量为 5。
- **`include_self` 存标志而非邮箱快照**：否则用户改邮箱后通知仍发往废弃地址；并对存量用户 `recipients` 里已有自己的情况做去重。
- **`starttls` 静默降级**：既有实现把失败吞掉，Task 9 改为失败即报错，并在 Task 16 把 `notification_tools` 也收敛到同一实现，避免两份逻辑漂移。
- **同步阻塞事件循环**：`send_mail` 是同步阻塞的，所有调用点（测试端点、审核发信、通知发送）都必须用 `run_in_threadpool` 包装。
- **SSO 覆盖手改邮箱**：Task 18 明确了「第三方未提供 email 时不清空」，但「提供了就覆盖」与 `real_name` 语义一致，属已知边界。
