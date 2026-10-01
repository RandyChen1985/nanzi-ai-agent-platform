# 注册申请通知管理员 实施计划

> **执行方式：** 本仓库会话内联执行，逐任务 TDD（`test-driven-development` 技能）。计划中的「提交」步骤一律替换为「运行验证」——本仓库禁止 Agent 自行 `git commit`，提交由用户明确指令触发。

**Goal:** 自助注册成功后，向所有填了邮箱的管理员账号（`role='admin'`）发送一封待审核通知邮件，发信失败绝不影响注册。

**Architecture:** 全部实现落在 `app/services/email_delivery_service.py`（正文构造函数与查库先例都已在该模块）。拆成三件：纯函数 `build_admin_review_notice`（正文）、纯查询 `resolve_admin_recipients`（收件人）、编排 `notify_admins_of_pending_registration`（查库→取设置→构造→发送）。注册端点仅把它登记为 `BackgroundTasks`，响应体逐字节不变。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / pytest（asyncio）；无前端改动、无迁移。

**依据规范：** [`docs/superpowers/specs/2026-10-01-admin-review-notice-design.md`](../specs/2026-10-01-admin-review-notice-design.md)

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/services/email_delivery_service.py` | Modify（+约 110 行） | 新增 3 个 classmethod（正文构造 / 收件人解析 / 编排） |
| `app/api/portal/endpoints/auth.py` | Modify（+约 6 行） | 注册端点接线 `BackgroundTasks` |
| `tests/test_admin_review_notice.py` | Create（约 11 个用例） | 全部行为测试与机制契约 |
| `tests/CHECKLIST.md` | Modify（+1 行） | 交付清单 |
| `docs/superpowers/specs/2026-10-01-admin-review-notice-design.md` | Modify（§7.2） | 补「注册者未填邮箱」的情形（原规范漏了） |

**为什么要动的文件这么少：** 本特性没有新配置项、没有迁移、没有前端，唯一的新逻辑是「查收件人 + 拼正文 + 发信」，因此集中在一个已有邮件模块里，避免为 3 个函数新建一个模块。

---

## Task 0: 补规范遗漏（注册者本身未填邮箱）

**Files:**
- Modify: `docs/superpowers/specs/2026-10-01-admin-review-notice-design.md`（§7.2）

- [ ] **Step 1: 为什么必须先补这一条**

`RegisterRequest.email` 是**选填**（`auth.py:117`），所以「注册者没填邮箱」是正常路径。原规范 §7.2 只写了 `邮箱：zhangsan@corp.example.com`，没写这条情形 —— 若不处理，正文会出现 `邮箱：` 这样的空值行，管理员无法判断是「没填」还是「系统丢了」。

- [ ] **Step 2: 在 §7.2 正文示例后追加一段**

在 §7.2 那段代码块之后、「- **待审核总数复用 ...**」之前插入：

```markdown
- **注册者未填邮箱时渲染「邮箱：未填写」**，而不是留一行空的 `邮箱：`：
  邮箱是选填字段，空值行会让管理员无法区分「注册者没填」与「系统取值失败」。
```

- [ ] **Step 3: 运行验证**

```bash
grep -n "邮箱：未填写" docs/superpowers/specs/2026-10-01-admin-review-notice-design.md
```
Expected: 命中 1 行。

---

## Task 1: 正文构造函数 `build_admin_review_notice`

**Files:**
- Test: `tests/test_admin_review_notice.py`（本任务创建）
- Modify: `app/services/email_delivery_service.py`（新增导入 + 在 `build_review_mail` 之后新增函数）

- [ ] **Step 1: 写失败测试（正文含全部字段）**

创建 `tests/test_admin_review_notice.py`：

```python
"""注册申请通知管理员：行为测试。

约定与邮件找回测试一致：@pytest.mark.no_infrastructure 表示「不需要 DB 也不需要 Redis」，
只用于纯逻辑用例；碰数据库的用例**不能**打这个标记（tests/conftest.py:37 会因此跳过
数据库连接池的销毁重建，导致 got Future attached to a different loop）。
"""

from datetime import datetime

import pytest

from app.services.email_delivery_service import build_admin_review_notice


@pytest.mark.no_infrastructure
def test_notice_body_contains_all_fields():
    subject, body = build_admin_review_notice(
        user_name="zhangsan",
        real_name="张三",
        email="zhangsan@corp.example.com",
        remark="市场部",
        registered_at=datetime(2026, 10, 1, 7, 39, 3),
        pending_count=3,
        sender_name="AI 智能体平台",
    )

    assert subject == "【AI 智能体平台】有待审核的账号申请"
    for expected in (
        "账号名：zhangsan",
        "用户姓名：张三",
        "邮箱：zhangsan@corp.example.com",
        "备注：市场部",
        "申请时间：2026-10-01 07:39:03",
        "当前待审核：3 条",
        "请到「用户管理 → 待审核」处理",
    ):
        assert expected in body, f"正文缺少：{expected}"
```

> 申请时间断言为什么是确定的：`format_platform_datetime` 对 naive datetime 按「已是本地墙钟时间」处理（`platform_timezone.py:126`），只做 `localize` 不做换算，因此格式化结果等于入参的墙钟值。

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: FAIL —— `ImportError: cannot import name 'build_admin_review_notice'`

- [ ] **Step 3: 补导入**

在 `app/services/email_delivery_service.py` 的导入区：

```python
import json
import logging
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime          # ← 新增
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, List, Literal, Optional, Tuple

from sqlalchemy import func, select     # ← 新增 func
from sqlalchemy.ext.asyncio import AsyncSession
```

- [ ] **Step 4: 写最小实现**

在 `build_review_mail` 函数之后新增：

```python
def build_admin_review_notice(
    *,
    user_name: str,
    real_name: str,
    email: str,
    remark: Optional[str],
    registered_at: Optional[datetime],
    pending_count: int,
    sender_name: str,
) -> Tuple[str, str]:
    """待审核通知邮件文案（发给管理员）。返回 (主题, 正文)。

    刻意的取舍（与 build_review_mail 同口径）：

    - **不放链接**：待审核页无法用 URL 直达——前端 Users.vue 的 activeView 是纯本地
      ref、不接受 URL 参数，/users?view=review 会落在默认列表页；放个落到列表页的
      链接反而误导。用户已明确否决「改前端加 query 支持」，故只给文字指引。
      附带好处：本函数不依赖 download_url_prefix，也就不存在「前缀没配就发不出去」。
    - **入参不含任何凭据**：签名只接受展示用字段，从来源上保证这封邮件不可能泄露
      口令或 API Key——比在正文里逐个回避敏感词可靠（后者会连正当提示一起禁掉）。
    """
    subject = f"【{sender_name}】有待审核的账号申请"

    lines = [
        "您好，管理员：",
        "",
        "平台有一条新的账号注册申请，需要审核：",
        "",
        f"账号名：{user_name}",
        f"用户姓名：{real_name}",
        # 邮箱是选填字段：空值行会让管理员分不清「没填」和「系统取值失败」
        f"邮箱：{email.strip() or '未填写'}",
    ]

    cleaned_remark = (remark or "").strip()
    if cleaned_remark:
        # 备注同为选填：为空时整行不渲染，避免出现空的「备注：」。
        # 上限 255 与 User.remark 列宽、RegisterRequest 的约定一致。
        lines.append(f"备注：{cleaned_remark[:255]}")

    lines += [
        f"申请时间：{format_platform_datetime(registered_at)}",
        f"当前待审核：{pending_count} 条",
        "",
        "请到「用户管理 → 待审核」处理。",
        "",
        "（本邮件由系统自动发送，请勿直接回复）",
    ]
    return subject, "\n".join(lines) + "\n"
```

并在该文件导入区加上平台时区工具：

```python
from app.services.platform_timezone import format_platform_datetime
```

> 若该导入会与既有导入顺序冲突（isort 风格），放在 `from sqlalchemy...` 之后、`logger = ...` 之前。

- [ ] **Step 5: 运行测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（1 passed）

- [ ] **Step 6: 补两个边界用例并跑**

追加：

```python
@pytest.mark.no_infrastructure
def test_notice_omits_blank_remark_line():
    """备注为空/纯空白时整行不渲染，避免出现空的「备注：」。"""
    for blank in (None, "", "   "):
        _, body = build_admin_review_notice(
            user_name="u", real_name="n", email="u@corp.example.com",
            remark=blank, registered_at=datetime(2026, 10, 1, 0, 0, 0),
            pending_count=0, sender_name="平台",
        )
        assert "备注：" not in body, f"remark={blank!r} 时不应渲染备注行"


@pytest.mark.no_infrastructure
def test_notice_marks_missing_registrant_email():
    """注册者未填邮箱时渲染「未填写」，而不是留一行空的「邮箱：」。"""
    _, body = build_admin_review_notice(
        user_name="u", real_name="n", email="   ", remark=None,
        registered_at=None, pending_count=1, sender_name="平台",
    )
    assert "邮箱：未填写" in body
    assert "邮箱：\n" not in body
```

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（3 passed）

---

## Task 2: 收件人解析 `resolve_admin_recipients`

**Files:**
- Test: `tests/test_admin_review_notice.py`（追加）
- Modify: `app/services/email_delivery_service.py`

- [ ] **Step 1: 写失败测试**

先在测试文件顶部补齐导入与建号范式（照抄既有 `tests/test_password_reset.py:95-118` 的写法，避免另起一套）：

```python
import uuid

from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.email_delivery_service import (
    EmailDeliveryService, build_admin_review_notice,
)


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _seed_user(user_name: str, *, role: str, email=None, status: int = 1):
    """建一个指定角色/邮箱的临时用户，返回 uid。忽略 generate_api_key 返回的明文 key。"""
    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="管理员通知测试", status=status, email=email,
            role=role, db=s,
        )
        return (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar_one()


async def _drop_user(user_name: str) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(delete(User).where(User.user_name == user_name))
        await s.commit()


@pytest.fixture
async def admin_recipients():
    """建两个管理员（有效邮箱、大小写不同的重复邮箱）+ 一个无邮箱管理员 + 一个普通用户。"""
    suffix = uuid.uuid4().hex[:6]
    names = {
        "admin_a": _name("zadm_a"),
        "admin_b": _name("zadm_b"),
        "admin_dup": _name("zadm_dup"),
        "admin_no_mail": _name("zadm_nomail"),
        "plain": _name("zadm_plain"),
    }
    emails = {
        "admin_a": f"Notify_{suffix}@corp.example.com",
        "admin_b": f"other_{suffix}@corp.example.com",
        # 与 admin_a 仅大小写不同 → 必须被去重成一条
        "admin_dup": f"notify_{suffix}@CORP.EXAMPLE.COM",
        "plain": f"plain_{suffix}@corp.example.com",
    }
    await _seed_user(names["admin_a"], role="admin", email=emails["admin_a"])
    await _seed_user(names["admin_b"], role="admin", email=emails["admin_b"])
    await _seed_user(names["admin_dup"], role="admin", email=emails["admin_dup"])
    await _seed_user(names["admin_no_mail"], role="admin", email=None)
    await _seed_user(names["plain"], role="user", email=emails["plain"])
    try:
        yield names, emails
    finally:
        for name in names.values():
            await _drop_user(name)


async def test_resolve_admin_recipients_filters_and_dedupes(admin_recipients):
    names, emails = admin_recipients
    async with AsyncSessionLocal() as s:
        recipients = await EmailDeliveryService.resolve_admin_recipients(s)

    lowered = [r.lower() for r in recipients]
    # 两个不同邮箱的管理员都在
    assert emails["admin_a"].lower() in lowered
    assert emails["admin_b"].lower() in lowered
    # 与 admin_a 仅大小写不同的那条被去重：该地址只出现一次
    assert lowered.count(emails["admin_a"].lower()) == 1
    # 无邮箱管理员与普通用户都不在收件人里
    assert names["admin_no_mail"] not in " ".join(recipients)
    assert emails["plain"].lower() not in lowered
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: FAIL —— `AttributeError: type object 'EmailDeliveryService' has no attribute 'resolve_admin_recipients'`

- [ ] **Step 3: 写实现**

在 `EmailDeliveryService` 内（`load_user_smtp_settings` 之后）新增：

```python
    @classmethod
    async def resolve_admin_recipients(cls, db: AsyncSession) -> List[str]:
        """取全部可收信的管理员邮箱，大小写不敏感去重。

        为什么必须同时判 NULL 与 TRIM<>''：邮箱列可空、且允许历史空串/空白写入，
        只判 IS NOT NULL 会让空串混进收件人列表，SMTP 端一旦拒绝该地址，**整封邮件
        都发不出去**，等于把「一个管理员没填好」升级成「所有人都收不到」。
        """
        from app.models.user import User

        rows = (
            await db.execute(
                select(User.email).where(
                    User.role == "admin",
                    User.email.isnot(None),
                    func.trim(User.email) != "",
                )
            )
        ).scalars().all()

        seen = set()
        recipients: List[str] = []
        for raw in rows:
            addr = (raw or "").strip()
            if not addr:
                continue
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            recipients.append(addr)
        return recipients
```

- [ ] **Step 4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（4 passed）

---

## Task 3: 编排 `notify_admins_of_pending_registration`

**Files:**
- Test: `tests/test_admin_review_notice.py`（追加）
- Modify: `app/services/email_delivery_service.py`

- [ ] **Step 1: 写失败测试（成功路径 + 无收件人路径）**

追加：

```python
class _FakeSmtpSettings:
    sender_name = "测试平台"


def _stub_global_smtp(monkeypatch, *, available: bool = True):
    """把全局 SMTP 设置替换掉，并把 send_mail 换成记录器。返回调用记录 list。"""
    calls: list = []

    async def _fake_load_global(cls=None):
        return _FakeSmtpSettings() if available else None

    async def _fake_send_mail(cls, settings, recipients, subject, body):
        calls.append({"recipients": list(recipients), "subject": subject, "body": body})
        return True, None

    monkeypatch.setattr(
        EmailDeliveryService, "load_global_smtp_settings", classmethod(_fake_load_global)
    )
    monkeypatch.setattr(
        EmailDeliveryService, "send_mail", classmethod(_fake_send_mail)
    )
    return calls


async def test_notify_sends_to_all_admins(admin_recipients, monkeypatch):
    names, emails = admin_recipients
    # 再建一个待审核的注册者（本函数的入参就是它的 id）
    registrant = _name("zadm_reg")
    uid = await _seed_user(
        registrant, role="user", email=f"{registrant}@corp.example.com", status=2
    )
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert sent is True and error is None
        assert len(calls) == 1, "应当只发一封（收件人放同一个信封），而不是每个管理员各发一封"
        lowered = [r.lower() for r in calls[0]["recipients"]]
        assert emails["admin_a"].lower() in lowered
        assert registrant in calls[0]["body"]
    finally:
        await _drop_user(registrant)


async def test_notify_skips_when_no_recipient(monkeypatch, admin_recipients):
    """没有任何可用收件人时：不发送、返回 (False, 原因)，且**不抛异常**。"""
    monkeypatch.setattr(
        EmailDeliveryService, "resolve_admin_recipients",
        classmethod(lambda cls, db: _empty()),
    )
    calls = _stub_global_smtp(monkeypatch, available=True)
    sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(999999)
    assert sent is False
    assert error and "管理员" in error
    assert calls == []
```

并在文件顶部补一个极小的协程辅助：

```python
async def _empty():
    return []
```

> 为什么用 `monkeypatch.setattr(EmailDeliveryService, ...)` 而不是改共享库里真实管理员的邮箱：后者会污染其它用例与共享数据；替换入口是本仓库既有做法（见 `resolve_self_email` 的注释）。

- [ ] **Step 2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: FAIL —— `AttributeError: ... has no attribute 'notify_admins_of_pending_registration'`

- [ ] **Step 3: 写实现**

在 `resolve_admin_recipients` 之后新增：

```python
    @classmethod
    async def notify_admins_of_pending_registration(
        cls, user_id: int, db: Optional[AsyncSession] = None
    ) -> Tuple[bool, Optional[str]]:
        """向所有填了邮箱的管理员发一封「有待审核的账号申请」。返回 (sent, error)。

        **尽力而为**：任何异常都只记日志。调用方是注册接口的后台任务——它既不该、
        也无法把失败回传给注册者，因为「是否发出通知」属于平台内部信息。

        session 生命周期：**绝不能**把请求作用域的 session 传进后台任务。响应返回后
        它已关闭，会抛「session 已关闭」；若恰好落在另一个事件循环上还会报
        `attached to a different loop`（本仓库已踩过一次同类问题）。因此沿用本模块
        既有范式（见 load_user_smtp_settings）：没给 session 就自建一个并自行关闭。

        端点传 None → 自建 session → 能读到刚提交的注册记录，因此待审核计数天然
        包含这条新申请。按 user_id 重查而不是传 ORM 对象，避免 detached instance。
        """
        from starlette.concurrency import run_in_threadpool

        from app.models.user import User
        from app.services.auth_service import AuthService

        own_session = db is None
        session = db
        if own_session:
            session = AsyncSessionLocal()
        try:
            settings = await cls.load_global_smtp_settings()
            if settings is None:
                logger.info(
                    "待审核通知未发送：全局邮件服务未启用 user_id=%s", user_id
                )
                return False, "平台邮件服务未启用或配置不完整"

            recipients = await cls.resolve_admin_recipients(session)
            if not recipients:
                # 邮箱是可选字段，存量管理员很可能都没填 —— 必须留下明确线索，
                # 否则本功能会静默失效而无人察觉
                logger.warning(
                    "待审核通知未发送：没有任何填了邮箱的管理员账号 user_id=%s",
                    user_id,
                )
                return False, "没有填了邮箱的管理员账号"

            row = (
                await session.execute(
                    select(
                        User.user_name, User.real_name, User.email,
                        User.remark, User.created_at,
                    ).where(User.id == user_id)
                )
            ).first()
            if row is None:
                logger.warning("待审核通知未发送：用户不存在 user_id=%s", user_id)
                return False, "用户不存在"

            pending_count = await AuthService.count_pending_registrations(db=session)
            subject, body = build_admin_review_notice(
                user_name=row.user_name,
                real_name=row.real_name or "",
                email=row.email or "",
                remark=row.remark,
                registered_at=row.created_at,
                pending_count=pending_count,
                sender_name=settings.sender_name,
            )
            ok, message = await run_in_threadpool(
                cls.send_mail, settings, recipients, subject, body
            )
            if not ok:
                logger.warning(
                    "待审核通知发送失败 user_id=%s 收件人数=%s reason=%s",
                    user_id, len(recipients), message,
                )
            return ok, (None if ok else message)
        except Exception as exc:  # noqa: BLE001
            # 后台任务的异常不会被响应吃掉，但也无人接收，冒泡只会污染日志
            logger.warning("待审核通知发送异常 user_id=%s err=%s", user_id, exc)
            return False, str(exc)
        finally:
            if own_session and session is not None:
                await session.close()
```

> `AsyncSessionLocal` 该文件已在 `load_user_smtp_settings` 内以局部导入方式引入（`from app.core.orm import AsyncSessionLocal`）；若该文件在别处未做顶层导入，这里同样用局部导入保持风格一致。

- [ ] **Step 4: 运行确认通过**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（6 passed）

- [ ] **Step 5: 补「邮件服务未启用」「SMTP 抛异常」「计数含新申请」三个用例**

```python
async def test_notify_skips_when_global_mail_disabled(admin_recipients, monkeypatch):
    calls = _stub_global_smtp(monkeypatch, available=False)
    sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(999999)
    assert sent is False and calls == []


async def test_notify_swallows_smtp_failure(admin_recipients, monkeypatch):
    """SMTP 抛异常也必须返回 (False, ...)，绝不冒泡到调用方。"""
    def _boom(cls, settings, recipients, subject, body):
        raise RuntimeError("SMTP 连接失败")

    async def _fake_load_global(cls=None):
        return _FakeSmtpSettings()

    monkeypatch.setattr(
        EmailDeliveryService, "load_global_smtp_settings", classmethod(_fake_load_global)
    )
    monkeypatch.setattr(EmailDeliveryService, "send_mail", classmethod(_boom))

    registrant = _name("zadm_boom")
    uid = await _seed_user(registrant, role="user", email=None, status=2)
    try:
        sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert sent is False and error is not None
    finally:
        await _drop_user(registrant)


async def test_pending_count_includes_the_new_registration(admin_recipients, monkeypatch):
    """计数必须与 /users/pending-count 同源，且包含刚提交的这条申请。"""
    calls = _stub_global_smtp(monkeypatch, available=True)
    registrant = _name("zadm_count")
    uid = await _seed_user(registrant, role="user", email=None, status=2)
    try:
        from app.services.auth_service import AuthService as _A

        async with AsyncSessionLocal() as s:
            expected = await _A.count_pending_registrations(db=s)
        await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert f"当前待审核：{expected} 条" in calls[0]["body"]
        assert expected >= 1
    finally:
        await _drop_user(registrant)
```

- [ ] **Step 6: 运行**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（9 passed）

---

## Task 4: 注册端点接线

**Files:**
- Test: `tests/test_admin_review_notice.py`（追加机制契约）
- Modify: `app/api/portal/endpoints/auth.py`（`register` 函数）

- [ ] **Step 1: 写机制契约测试**

```python
import inspect


def test_register_endpoint_schedules_notice_as_background_task():
    """发信必须是 background task，而不是 await。

    为什么用源码契约而不是行为断言：TestClient 会等 BackgroundTasks 跑完才把响应
    交给测试，「快慢」在测试里观测不到；但「SMTP 超时会阻塞注册接口」恰恰是要防的
    那个回归，所以对机制本身断言。
    """
    from app.api.portal.endpoints.auth import register

    source = inspect.getsource(register)
    assert "background_tasks.add_task(" in source
    assert "notify_admins_of_pending_registration" in source
    # 不允许同步 await 发信
    assert "await EmailDeliveryService.notify_admins_of_pending_registration" not in source
    # BackgroundTasks 必须是端点参数（否则 FastAPI 不会注入）
    assert "background_tasks" in inspect.signature(register).parameters
```

- [ ] **Step 2: 运行确认失败**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: FAIL（该用例失败，`background_tasks` 不在签名里）

- [ ] **Step 3: 改端点**

在 `app/api/portal/endpoints/auth.py` 的 `register` 签名中新增参数（放在 `db` 之前，保持 FastAPI 依赖参数在后的惯例）：

```python
async def register(
    http_request: Request,
    request: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db_session),
):
```

在文件既有导入处补：

```python
from fastapi import BackgroundTasks
```

在该端点 `return {"status": "success", "message": REGISTER_SUCCESS_MESSAGE}` **之前**插入：

```python
    # 通知管理员：尽力而为、走后台任务。
    # ① 必须传 None：绝不能把请求作用域的 session 带进后台任务（响应返回后它已关闭，
    #    跨事件循环还会报 attached to a different loop）；
    # ② 不 await：SMTP 是同步阻塞且有超时，放响应前会拖慢注册接口；
    # ③ 响应体保持不变：是否发出通知属平台内部信息，不能回传给注册者。
    from app.services.email_delivery_service import EmailDeliveryService

    background_tasks.add_task(
        EmailDeliveryService.notify_admins_of_pending_registration, user_id, None
    )
```

- [ ] **Step 4: 运行机制契约 + 注册相关回归**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
.venv/bin/python -m pytest tests/test_user_registration.py tests/test_user_email_and_mail_service.py -q -p no:randomly
```
Expected: 前者 PASS（10 passed）；后者全绿

---

## Task 5: 注册响应体不变 + 无收件人不影响注册（端到端）

**Files:**
- Test: `tests/test_admin_review_notice.py`（追加）

- [ ] **Step 1: 写端到端用例**

复用邮件找回测试的 TestClient 与 stub 写法（`_stub_mail` / `_stub_redis`），新增：

```python
async def test_register_response_body_identical_with_and_without_mail(monkeypatch):
    """邮件服务开/关两种情况下，注册响应体必须逐字节相同。

    否则等于对外泄露「管理员邮箱是否配好」这类内部状态。
    """
    bodies = []
    for available in (True, False):
        _stub_registration_enabled(monkeypatch, True)
        _stub_global_smtp(monkeypatch, available=available)
        name = _name("zadm_resp")
        payload = {
            "user_name": name,
            "real_name": "响应体测试",
            "email": f"{name}@corp.example.com",
            "password": "<符合等保复杂度的口令>",
            "remark": "备注",
        }
        try:
            response = await _post_register(payload)
            assert response.status_code == 200
            bodies.append(response.text)
        finally:
            await _drop_user(name)
    assert bodies[0] == bodies[1]


async def test_register_still_succeeds_when_no_admin_has_email(monkeypatch):
    """无可用收件人时注册仍成功（后台任务静默跳过）。"""
    _stub_registration_enabled(monkeypatch, True)
    _stub_global_smtp(monkeypatch, available=True)
    monkeypatch.setattr(
        EmailDeliveryService, "resolve_admin_recipients",
        classmethod(lambda cls, db: _empty()),
    )
    name = _name("zadm_noadm")
    try:
        response = await _post_register({
            "user_name": name, "real_name": "无管理员", "email": None,
            "password": "<符合等保复杂度的口令>", "remark": None,
        })
        assert response.status_code == 200
    finally:
        await _drop_user(name)
```

> `_stub_registration_enabled` / `_post_register` 若在既有测试里没有现成同名辅助，就按 `tests/test_user_registration.py` 里现成的注册端点测试方式改写（**照抄它的调用方式**，不要新建一套 TestClient 装配）；口令必须满足等保复杂度校验，同样照抄既有用例里的口令。

- [ ] **Step 2: 运行**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（12 passed）

---

## Task 6: 变异反向验证

**Files:** 无（只跑脚本，逐个变异后必须**字节级还原**）

- [ ] **Step 1: 逐条注入变异，确认对应用例失败**

用一次性脚本（**用完即删**）对 `app/services/email_delivery_service.py` 与 `auth.py` 注入：

| 变异 | 必须失败的用例 |
|---|---|
| 收件人查询去掉 `func.trim(User.email) != ""` | `test_resolve_admin_recipients_filters_and_dedupes` |
| 去掉大小写去重（直接 append） | `test_resolve_admin_recipients_filters_and_dedupes` |
| 收件人查询去掉 `User.role == "admin"` | `test_resolve_admin_recipients_filters_and_dedupes` |
| 无收件人时改为抛异常 | `test_notify_skips_when_no_recipient` |
| 去掉整个 `try/except` 兜底 | `test_notify_swallows_smtp_failure` |
| 备注为空时仍渲染「备注：」 | `test_notice_omits_blank_remark_line` |
| 计数改为自己写一个 where 重算 | `test_pending_count_includes_the_new_registration` |
| 端点改成 `await` 发信 | `test_register_endpoint_schedules_notice_as_background_task` |
| 端点把请求 session 传进后台任务（`..., db`） | `test_register_response_body_identical_with_and_without_mail`（或 session 已关闭报错） |

- [ ] **Step 2: 确认每个变异都被捕获，且每个变异后**字节级**还原**

脚本必须做到：读原文 → 断言变异前目标片段恰好出现 1 次 → 替换 → 跑对应用例 → 无论成败都在 `finally` 里写回原文 → 断言 `read_text() == 原文`。（本仓库会话内已有同款做法，照它写。）

- [ ] **Step 3: 还原后重跑全量**

```bash
.venv/bin/python -m pytest tests/test_admin_review_notice.py -q -p no:randomly
```
Expected: PASS（12 passed）

---

## Task 7: 回归与交付清单

**Files:**
- Modify: `tests/CHECKLIST.md`（在表头下一行插入 1 行，5 列）

- [ ] **Step 1: 跑后端全量回归**

```bash
.venv/bin/python -m pytest tests/api tests/core -q -p no:randomly
```
Expected: 基线为 **647 passed / 10 xfailed**；本次应在此基础上**增加** `tests/test_admin_review_notice.py` 的数量，不得有 failed。

- [ ] **Step 2: 跑前端契约（确认零前端改动未破坏任何契约）**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q -p no:randomly
```
Expected: **1387 passed / 1 skipped**（与本次改动前一致）

- [ ] **Step 3: 确认零迁移、零前端改动**

```bash
git status --short
```
Expected: 只出现 `app/services/email_delivery_service.py`、`app/api/portal/endpoints/auth.py`、`tests/test_admin_review_notice.py`、`tests/CHECKLIST.md`、`docs/superpowers/specs/...`、`docs/superpowers/plans/...`；**`db-prod/` 与 `db-prod-pg/` 下无任何新增文件**。

- [ ] **Step 4: 更新 `tests/CHECKLIST.md`**

在 `| 特性 / 修复项 | ... |` 表头下的第一行数据行位置插入一行（5 列：特性 / 文件 / 内容 / 验收状态 / 日期），内容涵盖：收件人口径（admin + 有效邮箱 + 去重）、跟随全局邮件服务、尽力而为不阻塞注册、响应体不变、不含链接的原因、计数同源、测试与反向验证结论。

- [ ] **Step 5: 汇报（不提交）**

列出改动文件、验证证据（各套用例数字、变异捕获数），提交留给用户明确指令。

---

## Self-Review

**1. 规范覆盖**

| 规范章节 | 落到的任务 |
|---|---|
| §3 现状核实 | 无需实现（证据） |
| §4 触发与时机 | Task 4 |
| §5 收件人 | Task 2 |
| §6 可用性 | Task 3 Step 5（未启用用例） |
| §7.1 主题 | Task 1 |
| §7.2 正文（含新增的未填邮箱情形） | Task 0 + Task 1 |
| §7.3 不放链接 | Task 1（实现即不含链接，注释说明原因） |
| §8 代码落点 | Task 1/2/3/4（三件 + 端点两行） |
| §9 会话生命周期 | Task 3（`own_session` 范式）+ Task 4（传 None）|
| §10 失败处理 | Task 3 Step 5 + Task 6 |
| §11 安全与滥用 | Task 1（签名不含凭据）、Task 4（响应体不变）、Task 5 |
| §12 测试计划 | Task 1/2/3/4/5（11 项 + 机制契约）|
| §13 风险 | Task 3（无收件人 WARNING 日志）|
| §14 验收标准 | Task 6 + Task 7 |
| §15 实施顺序 | 任务顺序即此 |

无缺口。

**2. 占位符扫描**

计划中的口令字段写成 `<符合等保复杂度的口令>` 是**唯一**的占位——它必须在 Task 5 实现时照抄既有注册用例的现成口令，因为口令复杂度规则由后端校验决定，计划里写死一个可能因规则调整而失效。这不是「待补充」，而是「必须从既有用例取值」的明确指令。

**3. 类型一致性**

- `build_admin_review_notice(*, ...)` 全程关键字调用（Task 1 定义、Task 3 调用）✓
- `resolve_admin_recipients(db) -> List[str]`（Task 2 定义、Task 3 调用、Task 3 测试 monkeypatch 的签名 `(cls, db)`）✓
- `notify_admins_of_pending_registration(user_id, db=None) -> Tuple[bool, Optional[str]]`（Task 3 定义、Task 4 端点传 `user_id, None`）✓
- 测试辅助 `_seed_user` / `_drop_user` / `_empty` / `_FakeSmtpSettings` / `_stub_global_smtp` 在所有引用它的任务里均已定义 ✓
- `AuthService.count_pending_registrations(db=session)` 签名匹配（`auth_service.py:516`）✓
