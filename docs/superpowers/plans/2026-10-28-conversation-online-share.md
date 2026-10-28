# 会话历史在线分享 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给会话历史加只读在线分享：公开 / 密钥访问、可设有效期，侧栏新增「我分享的」用于复制链接与密码、作废、查看打开次数。

**Architecture:** 分享即快照——创建时把会话的 `query`/`summary` 正文清洗后冻结进 `ai_conversation_shares.content_json`，之后原会话继续对话、被删除或 Redis TTL 到期都不影响分享页。所有者侧接口走 `/api/portal/chat-shares`（`require_api_key`），访客侧走 `/api/v1/share/*`（免鉴权的 `public_router`）；访客页是独立 public 路由 `/share/:shareId`，正文**复用** `MessageRenderer.vue` 并新增 `readonly` 开关，关掉依赖登录态与宿主画布的四类能力。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Pydantic 2（后端）；Vue 3 + TypeScript + Vite 7 + Tailwind 3（前端）；MySQL（`db-prod/`）与 PostgreSQL（`db-prod-pg/`）双份迁移；`cryptography.Fernet` 做密码与访问令牌；Redis 做解锁限流。

**任务总量：** 13 个任务，后端 6 个（迁移 → 模型 → 纯函数 → 仓储 → 所有者接口 → 访客接口），前端 7 个（有效期纯函数 → API 层 → 弹窗 → 列表面板 → 渲染器只读 → 分享页 → 接线回归）。

**设计文档：** `docs/superpowers/specs/2026-10-28-conversation-online-share-design.md`
**视觉稿：** `docs/mockups/2026-10-28-conversation-share.html`

---

## 执行前提（每次开工先读）

1. **不执行 `./dev.sh`、不执行 `db-prod/apply-sql.sh`、不直接改库**。迁移脚本只写文件，由用户在控制台执行。
2. **不执行 `git commit`**。每个任务结束只汇报「改了哪些文件、验证结果」，提交时机由用户决定。
3. 接口测试依赖真实 MySQL/PG 里的 `ai_conversation_shares` 表：**Task 1 完成后必须提醒用户执行迁移**，否则 Task 4 之后的测试会报表不存在。
4. 测试命令统一在仓库根目录执行，且**必须**用项目虚拟环境并显式给 PYTHONPATH：
   `PYTHONPATH=. .venv/bin/pytest <路径> -v`（直接 `pytest` 会 `ModuleNotFoundError: No module named 'app'`）。
   前端类型检查：`cd frontend && npx vue-tsc --noEmit`（基线干净，必须 exit 0）。
5. **迁移版本号（已按仓库实况调整，勿改回）**：MySQL `V197`、PG `V97`。
   原因：`db-prod/` 实际最高已是 `V194`（且已有两个 `V100-*`），而 `V195/V196` 与 PG `V95/V96`
   已被 `docs/superpowers/plans/2026-10-26-knowledge-base-hardening.md` 的
   `knowledge_health_snapshots` / `knowledge_access_requests` 占用。

---

## File Structure

**后端新增**

| 文件 | 职责 |
|---|---|
| `app/models/conversation_share.py` | ORM 模型，只描述表结构，不含业务 |
| `app/services/conversation_share_service.py` | 纯函数（密码 / 有效期 / 令牌 / 正文清洗）+ 业务（快照装配、增删查、访问计数） |
| `app/api/portal/endpoints/chat_shares.py` | 所有者接口：创建 / 列表 / 作废 |
| `app/api/v1/endpoints/share.py` | 访客接口：元信息 / 解锁 / 内容 |
| `db-prod/V197-create-conversation-shares.sql` | MySQL 迁移 |
| `db-prod-pg/V97-create-conversation-shares.sql` | PostgreSQL 迁移 |
| `tests/services/test_conversation_share_service.py` | 纯函数单测 |
| `tests/services/test_conversation_share_repository.py` | 快照装配与计数（需 DB） |
| `tests/api/portal/test_conversation_shares_api.py` | 所有者端点契约 |
| `tests/api/test_public_share_api.py` | 访客端点契约（含限流与计数） |
| `tests/test_conversation_share_migration_contract.py` | 两套迁移结构一致 |

**后端修改**

- `app/api/portal/api.py`：注册 `chat_shares.router`，前缀 `/chat-shares`
- `app/api/v1/api.py`：注册 `share.public_router`，前缀 `/share`

**前端新增**

| 文件 | 职责 |
|---|---|
| `frontend/src/utils/shareExpiry.ts` | 纯函数：有效期换算、密码生成、状态推导、剩余时间文案 |
| `frontend/src/api/chatShare.ts` | HTTP 封装（所有者三条 + 访客三条） |
| `frontend/src/components/ShareConversationDialog.vue` | 创建分享弹窗（配置态 + 结果态） |
| `frontend/src/components/SharedLinksPanel.vue` | 「我分享的」列表、复制、行内作废 |
| `frontend/src/views/SharedConversation.vue` | 公开只读分享页（加载 / 密码 / 内容 / 失效四态） |
| `tests/frontend/share_expiry.mjs` + `test_share_expiry_contract.py` | 有效期与密码生成行为测试 |
| `tests/frontend/message_renderer_readonly.mjs` + `test_message_renderer_readonly_contract.py` | 只读降级契约 |

**前端修改**

- `frontend/src/components/ChatHistorySidebar.vue`：tab 切换、分享按钮、`enableShare` prop
- `frontend/src/components/MessageRenderer.vue`：新增 `readonly` prop
- `frontend/src/views/EmbedChat.vue`：接线弹窗与分享列表
- `frontend/src/router/index.ts`：`/share/:shareId` public 路由

---

### Task 1: 双库迁移脚本 + 结构一致性测试（V197 / V97）

**Files:**
- Create: `db-prod/V197-create-conversation-shares.sql`
- Create: `db-prod-pg/V97-create-conversation-shares.sql`
- Test: `tests/test_conversation_share_migration_contract.py`

- [ ] **Step 1: 写 MySQL 迁移**

```sql
-- V197: 会话在线分享
-- 分享即快照：content_json 冻结创建那一刻的问答正文，之后原会话的任何变化都不影响分享页。
-- 过期不落库（expires_at 为 NULL 表示永久，过期状态由时间计算），避免引入定时任务。
CREATE TABLE IF NOT EXISTS `ai_conversation_shares` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `share_id` VARCHAR(32) NOT NULL COMMENT '对外链接 ID（secrets.token_urlsafe(18)）',
  `conversation_id` VARCHAR(50) DEFAULT NULL COMMENT '被分享会话 ID，仅用于追溯',
  `owner_user_id` VARCHAR(64) NOT NULL COMMENT '分享人用户 ID',
  `owner_username` VARCHAR(64) DEFAULT NULL COMMENT '分享人用户名（展示用）',
  `agent_id` VARCHAR(36) DEFAULT NULL COMMENT '分享时智能体 ID',
  `agent_name` VARCHAR(128) DEFAULT NULL COMMENT '分享时智能体显示名',
  `title` VARCHAR(500) DEFAULT NULL COMMENT '会话标题（首轮提问截断 120 字）',
  `access_mode` VARCHAR(16) NOT NULL DEFAULT 'secret' COMMENT 'public / secret',
  `secret_cipher` VARCHAR(512) DEFAULT NULL COMMENT '访问密码密文（Fernet，可回看）',
  `content_json` LONGTEXT COMMENT '冻结的消息快照 [{role, content, created_at}]',
  `message_count` INT NOT NULL DEFAULT 0 COMMENT '消息条数',
  `turn_count` INT NOT NULL DEFAULT 0 COMMENT '问答轮数',
  `expires_at` DATETIME DEFAULT NULL COMMENT '失效时间，NULL=永久',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active / revoked',
  `revoked_at` DATETIME DEFAULT NULL COMMENT '作废时间',
  `view_count` INT NOT NULL DEFAULT 0 COMMENT '成功打开次数',
  `last_viewed_at` DATETIME DEFAULT NULL COMMENT '最近一次成功打开',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ai_conversation_shares_share_id` (`share_id`),
  KEY `idx_ai_conversation_shares_owner` (`owner_user_id`, `created_at`),
  KEY `idx_ai_conversation_shares_conversation` (`conversation_id`),
  KEY `idx_ai_conversation_shares_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话在线分享（只读快照）';
```

- [ ] **Step 2: 写 PostgreSQL 迁移**

列与索引必须与 MySQL 版一一对应，只有类型写法不同（`BIGSERIAL` / `TIMESTAMP` / `TEXT`）。

```sql
-- V97: 会话在线分享（与 db-prod/V197 同结构，PG 方言）
CREATE TABLE IF NOT EXISTS ai_conversation_shares (
  id BIGSERIAL PRIMARY KEY,
  share_id VARCHAR(32) NOT NULL,
  conversation_id VARCHAR(50),
  owner_user_id VARCHAR(64) NOT NULL,
  owner_username VARCHAR(64),
  agent_id VARCHAR(36),
  agent_name VARCHAR(128),
  title VARCHAR(500),
  access_mode VARCHAR(16) NOT NULL DEFAULT 'secret',
  secret_cipher VARCHAR(512),
  content_json TEXT,
  message_count INTEGER NOT NULL DEFAULT 0,
  turn_count INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMP,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  revoked_at TIMESTAMP,
  view_count INTEGER NOT NULL DEFAULT 0,
  last_viewed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_ai_conversation_shares_share_id
  ON ai_conversation_shares (share_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_shares_owner
  ON ai_conversation_shares (owner_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_shares_conversation
  ON ai_conversation_shares (conversation_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversation_shares_created_at
  ON ai_conversation_shares (created_at);
```

- [ ] **Step 3: 写结构一致性测试**

```python
"""两套迁移必须同结构：漏一个列或索引，会在只跑其中一套的部署里变成运行时 500。"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.no_infrastructure

MYSQL_SQL = "db-prod/V197-create-conversation-shares.sql"
PG_SQL = "db-prod-pg/V97-create-conversation-shares.sql"

MYSQL_COLUMN_RE = re.compile(
    r"^\s*`([a-z_]+)`\s+(BIGINT|VARCHAR|INT|DATETIME|LONGTEXT)", re.MULTILINE
)
PG_COLUMN_RE = re.compile(
    r"^\s{2}([a-z_]+)\s+(BIGSERIAL|VARCHAR|INTEGER|TIMESTAMP|TEXT)", re.MULTILINE
)
INDEX_NAMES = (
    "uk_ai_conversation_shares_share_id",
    "idx_ai_conversation_shares_owner",
    "idx_ai_conversation_shares_conversation",
    "idx_ai_conversation_shares_created_at",
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_both_migrations_exist():
    assert (ROOT / MYSQL_SQL).exists()
    assert (ROOT / PG_SQL).exists()


def test_column_sets_are_identical():
    mysql_columns = {name for name, _type in MYSQL_COLUMN_RE.findall(_source(MYSQL_SQL))}
    pg_columns = {name for name, _type in PG_COLUMN_RE.findall(_source(PG_SQL))}
    assert mysql_columns == pg_columns, mysql_columns ^ pg_columns
    assert "content_json" in mysql_columns
    assert "secret_cipher" in mysql_columns


def test_index_names_match():
    mysql_sql = _source(MYSQL_SQL)
    pg_sql = _source(PG_SQL)
    for name in INDEX_NAMES:
        assert name in mysql_sql, f"MySQL 迁移缺索引 {name}"
        assert name in pg_sql, f"PG 迁移缺索引 {name}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_conversation_share_migration_contract.py -v`
Expected: 3 passed

- [ ] **Step 5: 提醒用户执行迁移（不自己跑）**

输出给用户：

```
迁移脚本已就绪，请在控制台执行：
  MySQL: ./db-prod/apply-sql.sh db-prod/V197-create-conversation-shares.sql
  PG:    ./db-prod-pg/apply-sql.sh db-prod-pg/V97-create-conversation-shares.sql
执行完再继续后续任务，否则接口测试会报表不存在。
```

---

### Task 2: ORM 模型

**Files:**
- Create: `app/models/conversation_share.py`
- Test: `tests/services/test_conversation_share_model.py`

- [ ] **Step 1: 写失败测试**

```python
"""模型契约：表名、长文本变体、索引名必须与迁移脚本一致。"""

import pytest
from sqlalchemy.dialects import mysql

from app.models.conversation_share import ConversationShare

pytestmark = pytest.mark.no_infrastructure


def test_table_and_key_columns():
    assert ConversationShare.__tablename__ == "ai_conversation_shares"
    columns = ConversationShare.__table__.columns
    for name in (
        "share_id", "conversation_id", "owner_user_id", "access_mode",
        "secret_cipher", "content_json", "expires_at", "status",
        "view_count", "last_viewed_at",
    ):
        assert name in columns, name
    assert columns["share_id"].unique is True
    assert columns["expires_at"].nullable is True


def test_content_json_uses_longtext_on_mysql():
    content_type = ConversationShare.__table__.columns["content_json"].type
    assert content_type.dialect_impl(mysql.dialect()).__class__.__name__ == "LONGTEXT"


def test_indexes_declared():
    names = {index.name for index in ConversationShare.__table__.indexes}
    assert "idx_ai_conversation_shares_owner" in names
    assert "idx_ai_conversation_shares_conversation" in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_conversation_share_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.conversation_share'`

- [ ] **Step 3: 写模型**

```python
"""会话在线分享表。

与 ``agent_share`` / ``skill_share`` 同一语义：分享即打快照，源会话后续被改、被删都不
影响已发出的分享。刻意不建物理外键——删除用户由管理侧统一清理，避免外键把会话删除
也一起卡住。

``content_json`` 用 LONGTEXT：长会话（数百轮）在 MySQL 严格模式下会把普通 TEXT
（65KB）整行打回。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql

from app.core.orm import Base


def _share_long_text() -> Text:
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class ConversationShare(Base):
    __tablename__ = "ai_conversation_shares"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    share_id = Column(String(32), nullable=False, unique=True, index=True, comment="对外链接 ID")
    conversation_id = Column(String(50), nullable=True, index=True, comment="被分享会话 ID（仅追溯）")
    owner_user_id = Column(String(64), nullable=False, index=True, comment="分享人用户 ID")
    owner_username = Column(String(64), nullable=True, comment="分享人用户名（展示用）")
    agent_id = Column(String(36), nullable=True, comment="分享时智能体 ID")
    agent_name = Column(String(128), nullable=True, comment="分享时智能体显示名")
    title = Column(String(500), nullable=True, comment="会话标题（首轮提问截断）")
    access_mode = Column(String(16), nullable=False, default="secret", comment="public / secret")
    secret_cipher = Column(String(512), nullable=True, comment="访问密码密文（Fernet）")
    content_json = Column(_share_long_text(), nullable=True, comment="冻结的消息快照")
    message_count = Column(Integer, nullable=False, default=0, comment="消息条数")
    turn_count = Column(Integer, nullable=False, default=0, comment="问答轮数")
    expires_at = Column(DateTime, nullable=True, comment="失效时间，NULL=永久")
    status = Column(String(16), nullable=False, default="active", comment="active / revoked")
    revoked_at = Column(DateTime, nullable=True, comment="作废时间")
    view_count = Column(Integer, nullable=False, default=0, comment="成功打开次数")
    last_viewed_at = Column(DateTime, nullable=True, comment="最近一次成功打开")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="创建时间")

    __table_args__ = (
        Index("idx_ai_conversation_shares_owner", "owner_user_id", "created_at"),
        Index("idx_ai_conversation_shares_conversation", "conversation_id"),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/services/test_conversation_share_model.py -v`
Expected: 3 passed

---

### Task 3: 服务层纯函数（密码 / 有效期 / 令牌 / 正文清洗）

**Files:**
- Create: `app/services/conversation_share_service.py`（本任务只写纯函数与常量，业务函数在 Task 4 追加）
- Test: `tests/services/test_conversation_share_service.py`

- [ ] **Step 1: 写失败测试**

```python
"""分享服务的纯函数契约：密码字符集、有效期换算、令牌时效、正文清洗。"""

from datetime import datetime, timedelta

import pytest

from app.services import conversation_share_service as svc

pytestmark = pytest.mark.no_infrastructure

NOW = datetime(2026, 10, 28, 13, 20, 0)


def test_generated_secret_avoids_confusable_characters():
    secrets_seen = {svc.generate_secret() for _ in range(50)}
    assert len(secrets_seen) > 40, "每次都相同说明没用系统随机源"
    for value in secrets_seen:
        assert len(value) == svc.SECRET_LENGTH
        assert not set(value) & set("0O1lI"), value
        assert all(ch.isalnum() for ch in value)


def test_custom_secret_length_is_validated():
    assert svc.normalize_secret("  abcd1234  ") == "abcd1234"
    with pytest.raises(ValueError):
        svc.normalize_secret("abc")
    with pytest.raises(ValueError):
        svc.normalize_secret("x" * 33)


@pytest.mark.parametrize(
    "preset,expected_delta",
    [("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)),
     ("7d", timedelta(days=7)), ("30d", timedelta(days=30))],
)
def test_preset_expiry(preset, expected_delta):
    assert svc.resolve_expires_at({"mode": "preset", "preset": preset}, now=NOW) == NOW + expected_delta


def test_permanent_and_custom_expiry():
    assert svc.resolve_expires_at({"mode": "permanent"}, now=NOW) is None
    assert svc.resolve_expires_at(
        {"mode": "custom", "value": 3, "unit": "day"}, now=NOW
    ) == NOW + timedelta(days=3)
    assert svc.resolve_expires_at(
        {"mode": "custom", "value": 6, "unit": "hour"}, now=NOW
    ) == NOW + timedelta(hours=6)
    with pytest.raises(ValueError):
        svc.resolve_expires_at({"mode": "custom", "value": 0, "unit": "day"}, now=NOW)
    with pytest.raises(ValueError):
        svc.resolve_expires_at({"mode": "custom", "value": 999, "unit": "day"}, now=NOW)


def test_access_token_roundtrip_and_expiry():
    token, expires_in = svc.issue_access_token("share-abc", NOW + timedelta(days=7), now=NOW)
    assert svc.verify_access_token(token, "share-abc", now=NOW + timedelta(hours=1)) is True
    # 跨分享复用必须失败
    assert svc.verify_access_token(token, "share-other", now=NOW) is False
    # 超过 12 小时上限即失效（即使分享本身还有效）
    assert svc.verify_access_token(token, "share-abc", now=NOW + timedelta(hours=13)) is False
    assert expires_in == int(timedelta(hours=12).total_seconds())


def test_token_cannot_outlive_share_expiry():
    token, expires_in = svc.issue_access_token("share-abc", NOW + timedelta(hours=2), now=NOW)
    assert expires_in == int(timedelta(hours=2).total_seconds())
    assert svc.verify_access_token(token, "share-abc", now=NOW + timedelta(hours=3)) is False


def test_secret_roundtrip_and_tamper():
    cipher = svc.encrypt_secret("abcd1234")
    assert cipher != "abcd1234"
    assert svc.decrypt_secret(cipher) == "abcd1234"
    assert svc.decrypt_secret("not-a-valid-cipher") is None


def test_sanitize_removes_internal_reasoning_and_sql():
    raw = (
        "结论如下。\n"
        "<thought>内部想了很久</thought>\n"
        ":::analysis 分析\n这里是推理\n:::\n"
        "<sql_plan>{\"sql\":\"select * from secret_table\"}</sql_plan>\n"
        "| 月份 | GMV |\n| --- | --- |\n| 09 | 463.9 |\n"
    )
    cleaned = svc.sanitize_assistant_content(raw)
    assert "内部想了很久" not in cleaned
    assert "这里推理" not in cleaned
    assert "secret_table" not in cleaned
    assert "463.9" in cleaned, "正文表格不能一起被吃掉"


def test_sanitize_downgrades_generated_file_links():
    raw = "已生成文件：[分析结果.xlsx](/api/v1/chat/generated-files/" + "a" * 32 + "?token=xyz)"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "generated-files" not in cleaned
    assert "分析结果.xlsx" in cleaned
    assert "文件未随分享提供" in cleaned


def test_sanitize_strips_quick_buttons():
    raw = "请选择：[继续分析](quick:%E7%BB%A7%E7%BB%AD) 或 [查看明细](quick:detail)"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "quick:" not in cleaned
    assert "继续分析" not in cleaned


def test_status_derivation():
    class Row:
        status = "active"
        expires_at = NOW - timedelta(minutes=1)

    assert svc.derive_share_status(Row(), now=NOW) == "expired"
    Row.status = "revoked"
    assert svc.derive_share_status(Row(), now=NOW) == "revoked"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_conversation_share_service.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError: generate_secret`

- [ ] **Step 3: 实现纯函数**

```python
"""会话在线分享的业务逻辑。

设计约束（与设计文档一致）：
- 分享即快照：``content_json`` 存创建那一刻的正文，读分享不查原会话；
- 密码存**密文**而不是哈希：需求要求列表里能反复「复制密码」，哈希不可逆；
- 失效统一 404：不存在 / 已作废 / 已过期对访客是同一句话，避免被枚举探测。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.models.conversation_share import ConversationShare

logger = logging.getLogger(__name__)

SHARE_ID_BYTES = 18
SECRET_LENGTH = 8
SECRET_MIN_LENGTH = 6
SECRET_MAX_LENGTH = 32
#: 去掉 0/O/1/l/I：口头转述与手抄密码时最容易出错的字符
SECRET_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

TOKEN_MAX_HOURS = 12
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_TURNS = 500
TITLE_MAX_LENGTH = 120

UNLOCK_FAIL_LIMIT = 5
UNLOCK_FAIL_WINDOW_SECONDS = 600

PRESET_TTL = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
CUSTOM_HOUR_RANGE = (1, 24 * 365)
CUSTOM_DAY_RANGE = (1, 365)

#: 快捷按钮语法 ``[标签](quick:命令)`` 的简化匹配（不处理命令里带右括号的极端情况，
#: 分享正文只做清理，残留的 ``quick:`` 片段由末尾兜底再扫一遍）
QUICK_LINK_RE = re.compile(r"(?:\[|【)[^\]】]+?(?:\]|】)\s*\(\s*quick:[^)]*\)\s*", re.IGNORECASE)
```

继续实现（同一文件，接在常量之后）：

```python
def _share_fernet() -> Fernet:
    """从平台 ``ENCRYPTION_KEY`` 派生分享专用密钥。

    刻意再 hash 一层：不与 API Key 共用同一把 Fernet key，避免一处泄露波及另一处。
    """
    raw = str(getattr(settings, "ENCRYPTION_KEY", "") or "").encode("utf-8")
    if not raw:
        raise ValueError("ENCRYPTION_KEY is required for conversation sharing")
    derived = hashlib.sha256(b"conversation-share:v1:" + raw).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def generate_secret(length: int = SECRET_LENGTH) -> str:
    return "".join(secrets.choice(SECRET_ALPHABET) for _ in range(length))


def normalize_secret(value: Optional[str]) -> str:
    """校验自定义密码；空白与长度不合法直接拒绝，不静默截断。"""
    secret = str(value or "").strip()
    if not (SECRET_MIN_LENGTH <= len(secret) <= SECRET_MAX_LENGTH):
        raise ValueError(f"访问密码长度需在 {SECRET_MIN_LENGTH}-{SECRET_MAX_LENGTH} 位之间")
    return secret


def resolve_expires_at(expiry: Dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    """把前端有效期选项换算成绝对时间；``None`` 表示永久。"""
    base = now or datetime.now()
    mode = str(expiry.get("mode") or "preset").strip().lower()

    if mode == "permanent":
        return None

    if mode == "preset":
        preset = str(expiry.get("preset") or "7d").strip().lower()
        delta = PRESET_TTL.get(preset)
        if delta is None:
            raise ValueError(f"不支持的有效期预设：{preset}")
        return base + delta

    if mode == "custom":
        try:
            value = int(expiry.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError("自定义有效期需要正整数") from exc
        unit = str(expiry.get("unit") or "day").strip().lower()
        if unit == "hour":
            low, high = CUSTOM_HOUR_RANGE
            if not (low <= value <= high):
                raise ValueError(f"小时数需在 {low}-{high} 之间")
            return base + timedelta(hours=value)
        if unit == "day":
            low, high = CUSTOM_DAY_RANGE
            if not (low <= value <= high):
                raise ValueError(f"天数需在 {low}-{high} 之间")
            return base + timedelta(days=value)
        raise ValueError(f"不支持的时间单位：{unit}")

    raise ValueError(f"不支持的有效期模式：{mode}")


def encrypt_secret(plaintext: str) -> str:
    """密码密文：Fernet token 再 base64 一层，长度约 140 字符，稳稳落在 VARCHAR(512) 内。"""
    token = _share_fernet().encrypt(plaintext.encode("utf-8"))
    return base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_secret(ciphertext: str) -> Optional[str]:
    """解密失败返回 None：密钥轮换后老分享的密码不可用，调用方按「需重建」提示。"""
    try:
        raw = base64.urlsafe_b64decode(str(ciphertext or "").encode("ascii"))
        return _share_fernet().decrypt(raw).decode("utf-8")
    except (InvalidToken, ValueError, TypeError, AttributeError):
        logger.warning("Failed to decrypt conversation share secret")
        return None


def verify_secret(ciphertext: str, candidate: str) -> bool:
    expected = decrypt_secret(ciphertext)
    if expected is None:
        return False
    return hmac.compare_digest(expected, str(candidate or ""))


def issue_access_token(
    share_id: str,
    share_expires_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> Tuple[str, int]:
    """签发短期访问令牌：有效期取 min(12h, 分享剩余有效期)。"""
    base = now or datetime.now()
    token_exp = base + timedelta(hours=TOKEN_MAX_HOURS)
    if share_expires_at is not None and share_expires_at < token_exp:
        token_exp = share_expires_at
    payload = {"share_id": share_id, "exp": int(token_exp.timestamp()), "v": 1}
    token = _share_fernet().encrypt(json.dumps(payload).encode("utf-8"))
    encoded = base64.urlsafe_b64encode(token).decode("ascii")
    return encoded, max(0, int((token_exp - base).total_seconds()))


def verify_access_token(token: str, share_id: str, now: Optional[datetime] = None) -> bool:
    try:
        raw = base64.urlsafe_b64decode(str(token or "").encode("ascii"))
        payload = json.loads(_share_fernet().decrypt(raw).decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, AttributeError):
        return False
    if str(payload.get("share_id") or "") != share_id:
        return False
    try:
        exp = int(payload.get("exp"))
    except (TypeError, ValueError):
        return False
    return (now or datetime.now()).timestamp() < exp


THOUGHT_RE = re.compile(r"<thought>[\s\S]*?</thought>", re.IGNORECASE)
ANALYSIS_RE = re.compile(r":::analysis[^\n]*\n[\s\S]*?\n:::", re.IGNORECASE)
SQL_PLAN_RE = re.compile(r"<sql_plan>[\s\S]*?</sql_plan>", re.IGNORECASE)
GENERATED_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]*?/api/v1/chat/generated-files/[^\s)]*?)\)", re.IGNORECASE
)
BARE_GENERATED_URL_RE = re.compile(
    r"https?://\S*?/api/v1/chat/generated-files/\S*", re.IGNORECASE
)


def sanitize_assistant_content(text: str) -> str:
    """清洗助手正文：只留对外的结论，不带内部推理、SQL 与需要登录的下载链接。"""
    value = str(text or "")
    value = THOUGHT_RE.sub("", value)
    value = ANALYSIS_RE.sub("", value)
    value = SQL_PLAN_RE.sub("", value)
    value = QUICK_LINK_RE.sub("", value)
    value = GENERATED_LINK_RE.sub(lambda m: f"📎 {m.group(1)}（文件未随分享提供）", value)
    value = BARE_GENERATED_URL_RE.sub("📎 附件（文件未随分享提供）", value)
    return value.strip()


def sanitize_user_content(text: str) -> str:
    return str(text or "").strip()


def derive_share_status(row: Any, now: Optional[datetime] = None) -> Literal["active", "expired", "revoked"]:
    if str(getattr(row, "status", "active")) == "revoked":
        return "revoked"
    expires_at = getattr(row, "expires_at", None)
    if expires_at is not None and expires_at <= (now or datetime.now()):
        return "expired"
    return "active"


def is_share_reachable(row: Any, now: Optional[datetime] = None) -> bool:
    return derive_share_status(row, now=now) == "active"
```

> 实现细节：`QUICK_LINK_RE` 只在常量区定义一次；`sanitize_*` 里的正则（`THOUGHT_RE` 等）紧邻使用处定义，便于按需增删。

- [ ] **Step 4: 跑测试确认通过；不通过就改实现，不改断言**

Run: `pytest tests/services/test_conversation_share_service.py -v`
Expected: 全部 passed（14 个用例）

---

### Task 4: 快照装配与分享读写（依赖数据库）

**Files:**
- Modify: `app/services/conversation_share_service.py`（追加业务函数）
- Test: `tests/services/test_conversation_share_repository.py`

- [ ] **Step 1: 写失败测试**

```python
"""快照装配与计数：分享必须冻结正文，且读内容才 +1。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory
from app.services import conversation_share_service as svc

pytestmark = pytest.mark.asyncio

CONV_ID = "conv-share-test-0001"
USER_ID = "share-test-user"


@pytest.fixture
async def seeded_turns():
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        for index, (query, summary) in enumerate([
            ("第一问", "第一答 <thought>内部</thought>"),
            ("第二问", "第二答：463.9"),
        ]):
            session.add(AgentExecutionHistory(
                agent_id="agent-test",
                trace_id=f"trace-share-{index}",
                conversation_id=CONV_ID,
                user_id=USER_ID,
                username="share_tester",
                query=query,
                summary=summary,
                status="success",
                created_at=datetime.now() - timedelta(minutes=10 - index),
            ))
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        await session.execute(delete(svc.ConversationShare).where(
            svc.ConversationShare.conversation_id == CONV_ID
        ))
        await session.commit()


async def test_snapshot_freezes_sanitized_content(seeded_turns):
    async with AsyncSessionLocal() as session:
        snapshot = await svc.assemble_snapshot(
            session, user_id=USER_ID, conversation_id=CONV_ID
        )
    assert snapshot is not None
    assert snapshot.turn_count == 2
    assert [m["role"] for m in snapshot.messages] == ["user", "assistant", "user", "assistant"]
    assert "内部" not in snapshot.messages[1]["content"]
    assert snapshot.title == "第一问"


async def test_snapshot_returns_none_for_foreign_conversation(seeded_turns):
    async with AsyncSessionLocal() as session:
        snapshot = await svc.assemble_snapshot(
            session, user_id="someone-else", conversation_id=CONV_ID
        )
    assert snapshot is None


async def test_create_list_revoke_and_count(seeded_turns):
    async with AsyncSessionLocal() as session:
        row = await svc.create_share(
            session,
            owner_user_id=USER_ID,
            owner_username="share_tester",
            conversation_id=CONV_ID,
            access_mode="secret",
            secret="abcd1234",
            expires_at=datetime.now() + timedelta(days=7),
        )
    assert row.share_id and len(row.share_id) >= 20
    assert row.view_count == 0

    async with AsyncSessionLocal() as session:
        items, total = await svc.list_shares(session, user_id=USER_ID, page=1, page_size=20)
    assert total >= 1
    assert any(item.share_id == row.share_id for item in items)

    async with AsyncSessionLocal() as session:
        content = await svc.read_share_content(
            session, share_id=row.share_id, token=None,
            viewer_user_id=USER_ID, client_ip="127.0.0.1",
        )
    assert len(content["messages"]) == 4

    async with AsyncSessionLocal() as session:
        refreshed = await svc.get_share(session, row.share_id)
    assert refreshed.view_count == 1
    assert refreshed.last_viewed_at is not None

    async with AsyncSessionLocal() as session:
        assert await svc.revoke_share(session, user_id=USER_ID, share_id=row.share_id) is True
        assert await svc.revoke_share(session, user_id=USER_ID, share_id=row.share_id) is True  # 幂等
        assert await svc.revoke_share(session, user_id="other", share_id=row.share_id) is False

    async with AsyncSessionLocal() as session:
        with pytest.raises(svc.ShareNotFoundError):
            await svc.read_share_content(
                session, share_id=row.share_id, token=None,
                viewer_user_id=USER_ID, client_ip="127.0.0.1",
            )


async def test_secret_share_requires_valid_token(seeded_turns):
    async with AsyncSessionLocal() as session:
        row = await svc.create_share(
            session, owner_user_id=USER_ID, owner_username="share_tester",
            conversation_id=CONV_ID, access_mode="secret", secret="abcd1234",
            expires_at=None,
        )
    async with AsyncSessionLocal() as session:
        with pytest.raises(svc.ShareAccessDeniedError):
            await svc.read_share_content(
                session, share_id=row.share_id, token=None,
                viewer_user_id="visitor", client_ip="127.0.0.1",
            )
    # 解锁后拿到的 token 可以读
    async with AsyncSessionLocal() as session:
        token, _ttl = await svc.unlock_share(
            session, share_id=row.share_id, secret="abcd1234", client_ip="127.0.0.1"
        )
        content = await svc.read_share_content(
            session, share_id=row.share_id, token=token,
            viewer_user_id=None, client_ip="127.0.0.1",
        )
    assert content["title"]
    # 分享人自己带登录态直接读，不需要 token
    async with AsyncSessionLocal() as session:
        owner_view = await svc.read_share_content(
            session, share_id=row.share_id, token=None,
            viewer_user_id=USER_ID, client_ip="127.0.0.1",
        )
    assert owner_view["messages"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_conversation_share_repository.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'assemble_snapshot'`

- [ ] **Step 3: 追加业务实现**

```python
class ShareNotFoundError(Exception):
    """链接不存在 / 已作废 / 已过期——对外统一 404，不区分原因。"""


class ShareAccessDeniedError(Exception):
    """密钥模式未解锁，或令牌无效。"""


class ShareContentTooLargeError(Exception):
    """会话过长，冻结快照会撑爆单行/单次响应。"""


@dataclass
class Snapshot:
    title: str
    agent_id: str
    agent_name: str
    messages: List[Dict[str, Any]]
    turn_count: int


async def assemble_snapshot(db, *, user_id: str, conversation_id: str) -> Optional[Snapshot]:
    """按归属拉全量轮次并组装冻结快照；找不到归属返回 None（端点转 404）。"""
    from sqlalchemy import func, select

    from app.models.audit import AgentExecutionHistory as History
    from app.services.ai.history_query import build_history_query

    count_query = select(func.count(History.id)).where(
        History.conversation_id == conversation_id,
        History.user_id == user_id,
    )
    total = int((await db.execute(count_query)).scalar() or 0)
    if total == 0:
        return None
    if total > MAX_SNAPSHOT_TURNS:
        raise ShareContentTooLargeError(f"会话共 {total} 轮，超过 {MAX_SNAPSHOT_TURNS} 轮上限")

    query, _count = build_history_query(
        page=1, page_size=MAX_SNAPSHOT_TURNS,
        user_id=user_id, conversation_id=conversation_id,
    )
    query = query.order_by(None).order_by(History.created_at.asc(), History.id.asc())
    rows = list((await db.execute(query)).scalars().all())
    if not rows:
        return None

    messages: List[Dict[str, Any]] = []
    for row in rows:
        created_at = row.created_at.isoformat() if row.created_at else None
        question = sanitize_user_content(row.query)
        answer = sanitize_assistant_content(row.summary)
        if question:
            messages.append({"role": "user", "content": question, "created_at": created_at})
        if answer:
            messages.append({"role": "assistant", "content": answer, "created_at": created_at})

    payload_size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    if payload_size > MAX_SNAPSHOT_BYTES:
        raise ShareContentTooLargeError("会话正文超过 2 MB，暂不支持在线分享")

    title = ""
    for message in messages:
        if message["role"] == "user":
            title = str(message["content"]).replace("\n", " ")[:TITLE_MAX_LENGTH]
            break

    agent_id = str(rows[-1].agent_id or "")
    agent_name = await _resolve_agent_name(db, agent_id)
    return Snapshot(
        title=title, agent_id=agent_id, agent_name=agent_name,
        messages=messages, turn_count=len(rows),
    )


async def _resolve_agent_name(db, agent_id: str) -> str:
    """冻结分享时的智能体显示名：会话存的是 ID，展示要给人看的名字。"""
    if not agent_id:
        return ""
    from sqlalchemy import select

    from app.models.agent import AIAgent

    row = (await db.execute(
        select(AIAgent.display_name, AIAgent.name).where(AIAgent.id == agent_id)
    )).first()
    if row is None:
        return agent_id
    return str(row[0] or row[1] or agent_id)
```

> 智能体表是 `app/models/agent.py::AIAgent`（`ai_agents`），展示名优先 `display_name`，回退 `name`。

创建 / 列表 / 作废 / 读取：

```python
async def create_share(
    db, *, owner_user_id: str, owner_username: str, conversation_id: str,
    access_mode: str, secret: Optional[str], expires_at: Optional[datetime],
) -> ConversationShare:
    snapshot = await assemble_snapshot(db, user_id=owner_user_id, conversation_id=conversation_id)
    if snapshot is None:
        raise ShareNotFoundError("conversation not found")

    mode = "public" if access_mode == "public" else "secret"
    secret_cipher = None
    if mode == "secret":
        secret_cipher = encrypt_secret(normalize_secret(secret))

    row = ConversationShare(
        share_id=secrets.token_urlsafe(SHARE_ID_BYTES)[:32],
        conversation_id=conversation_id,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        agent_id=snapshot.agent_id or None,
        agent_name=snapshot.agent_name or None,
        title=snapshot.title or None,
        access_mode=mode,
        secret_cipher=secret_cipher,
        content_json=json.dumps(snapshot.messages, ensure_ascii=False),
        message_count=len(snapshot.messages),
        turn_count=snapshot.turn_count,
        expires_at=expires_at,
        status="active",
        view_count=0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def list_shares(db, *, user_id: str, page: int, page_size: int, status: Optional[str] = None):
    from sqlalchemy import func, select

    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), 100)
    conditions = [ConversationShare.owner_user_id == user_id, ConversationShare.conversation_id.isnot(None)]
    total = int((await db.execute(select(func.count(ConversationShare.id)).where(*conditions))).scalar() or 0)
    rows = (await db.execute(
        select(ConversationShare).where(*conditions)
        .order_by(ConversationShare.created_at.desc(), ConversationShare.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    items = [row for row in rows if status in (None, "", "all") or derive_share_status(row) == status]
    return items, total


async def get_share(db, share_id: str) -> Optional[ConversationShare]:
    from sqlalchemy import select

    return (await db.execute(
        select(ConversationShare).where(ConversationShare.share_id == share_id)
    )).scalar_one_or_none()


async def revoke_share(db, *, user_id: str, share_id: str) -> bool:
    row = await get_share(db, share_id)
    if row is None or str(row.owner_user_id) != str(user_id):
        return False
    if row.status != "revoked":
        row.status = "revoked"
        row.revoked_at = datetime.now()
        await db.commit()
    return True


async def publish_public_meta(db, share_id: str) -> Dict[str, Any]:
    row = await get_share(db, share_id)
    if row is None or not is_share_reachable(row):
        raise ShareNotFoundError(share_id)
    remaining = None
    if row.expires_at is not None:
        remaining = max(0, int((row.expires_at - datetime.now()).total_seconds()))
    return {
        "title": row.title or "",
        "agent_name": row.agent_name or "",
        "access_mode": row.access_mode,
        "status": derive_share_status(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "remaining_seconds": remaining,
        "message_count": int(row.message_count or 0),
        "turn_count": int(row.turn_count or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "owner_display": row.owner_username or "",
    }


async def unlock_share(db, *, share_id: str, secret: str, client_ip: str) -> Tuple[str, int]:
    row = await get_share(db, share_id)
    if row is None or not is_share_reachable(row):
        raise ShareNotFoundError(share_id)
    if row.access_mode != "secret":
        raise ValueError("public share does not need unlocking")

    await assert_unlock_allowed(share_id, client_ip)
    if not verify_secret(row.secret_cipher or "", secret):
        await register_unlock_failure(share_id, client_ip)
        raise ShareAccessDeniedError("invalid secret")

    await clear_unlock_failures(share_id, client_ip)
    return issue_access_token(row.share_id, row.expires_at)


async def read_share_content(
    db, *, share_id: str, token: Optional[str], viewer_user_id: Optional[str], client_ip: str,
) -> Dict[str, Any]:
    row = await get_share(db, share_id)
    if row is None or not is_share_reachable(row):
        raise ShareNotFoundError(share_id)

    is_owner = bool(viewer_user_id) and str(viewer_user_id) == str(row.owner_user_id)
    if row.access_mode == "secret" and not is_owner:
        if not token or not verify_access_token(token, row.share_id):
            raise ShareAccessDeniedError("token required")

    from sqlalchemy import update

    now = datetime.now()
    await db.execute(
        update(ConversationShare)
        .where(ConversationShare.id == row.id)
        .values(
            view_count=ConversationShare.view_count + 1,
            last_viewed_at=now,
        )
    )
    await db.commit()

    return {
        "title": row.title or "",
        "agent_name": row.agent_name or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "messages": json.loads(row.content_json or "[]"),
    }
```

限流（Redis）：

```python
def _unlock_fail_key(share_id: str, client_ip: str) -> str:
    return f"share:unlock:fail:{share_id}:{client_ip or 'unknown'}"


async def assert_unlock_allowed(share_id: str, client_ip: str) -> None:
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    raw = await redis.get(_unlock_fail_key(share_id, client_ip))
    if raw is not None and int(raw or 0) >= UNLOCK_FAIL_LIMIT:
        raise ShareRateLimitedError("too many attempts")


async def register_unlock_failure(share_id: str, client_ip: str) -> None:
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    key = _unlock_fail_key(share_id, client_ip)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, UNLOCK_FAIL_WINDOW_SECONDS)


async def clear_unlock_failures(share_id: str, client_ip: str) -> None:
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        return
    await redis.delete(_unlock_fail_key(share_id, client_ip))
```

再补 `ShareRateLimitedError` 异常类和 `from dataclasses import dataclass`、`from typing import List` 等 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/services/test_conversation_share_repository.py -v`
Expected: 4 passed（需 Task 1 的迁移已在库里执行）


---

### Task 5: 所有者端点 `/api/portal/chat-shares`

**Files:**
- Create: `app/api/portal/endpoints/chat_shares.py`
- Modify: `app/api/portal/api.py`
- Test: `tests/api/portal/test_conversation_shares_api.py`

- [ ] **Step 1: 写失败测试**

```python
"""所有者端点的行为契约：归属校验、回显密码、作废幂等、列表状态。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory
from app.services import conversation_share_service as svc
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio

CONV_ID = "conv-share-api-0001"


@pytest.fixture
async def owner(valid_api_key):
    async with AsyncSessionLocal() as session:
        info = await AuthService.verify_api_key(valid_api_key, session)
    assert info and info.get("user_id")
    yield str(info["user_id"]), str(info.get("username") or info.get("user_name") or "tester")


@pytest.fixture
async def seeded_history(owner):
    user_id, _name = owner
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        for index, (query, summary) in enumerate([("问一", "答一"), ("问二", "答二"), ("问三", "答三")]):
            session.add(AgentExecutionHistory(
                agent_id="agent-api-test", trace_id=f"trace-api-{index}",
                conversation_id=CONV_ID, user_id=user_id, username="tester",
                query=query, summary=summary, status="success",
                created_at=datetime.now() - timedelta(minutes=30 - index),
            ))
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        await session.execute(delete(svc.ConversationShare).where(
            svc.ConversationShare.conversation_id == CONV_ID
        ))
        await session.commit()


def _headers(valid_api_key: str) -> dict:
    return {"X-API-Key": valid_api_key}


async def test_create_share_returns_url_and_secret(client, valid_api_key, seeded_history):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={
            "conversation_id": CONV_ID,
            "access_mode": "secret",
            "secret": "abcd1234",
            "expiry": {"mode": "preset", "preset": "7d"},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["share_id"]
    assert f"/share/{data['share_id']}" in data["url"]
    assert data["secret"] == "abcd1234"
    assert data["expires_at"] is not None
    assert data["turn_count"] == 3


async def test_create_public_permanent_share_has_no_expiry(client, valid_api_key, seeded_history):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "public", "expiry": {"mode": "permanent"}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["expires_at"] is None
    assert data["secret"] is None
    assert data["access_mode"] == "public"


async def test_create_rejects_missing_or_foreign_conversation(client, valid_api_key):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": "conv-not-mine", "access_mode": "public",
              "expiry": {"mode": "preset", "preset": "7d"}},
    )
    assert resp.status_code == 404


async def test_create_rejects_bad_secret_length(client, valid_api_key, seeded_history):
    resp = await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "secret", "secret": "ab",
              "expiry": {"mode": "preset", "preset": "7d"}},
    )
    assert resp.status_code == 400


async def test_list_and_revoke_flow(client, valid_api_key, seeded_history):
    created = (await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "secret", "secret": "abcd1234",
              "expiry": {"mode": "preset", "preset": "24h"}},
    )).json()["data"]

    listing = (await client.get("/api/portal/chat-shares", headers=_headers(valid_api_key))).json()["data"]
    item = next(row for row in listing["items"] if row["share_id"] == created["share_id"])
    assert item["status"] == "active"
    assert item["view_count"] == 0
    assert item["secret"] == "abcd1234"

    revoked = await client.post(
        f"/api/portal/chat-shares/{created['share_id']}/revoke",
        headers=_headers(valid_api_key),
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["revoked"] is True

    again = (await client.get("/api/portal/chat-shares", headers=_headers(valid_api_key))).json()["data"]
    item = next(row for row in again["items"] if row["share_id"] == created["share_id"])
    assert item["status"] == "revoked"


async def test_revoke_foreign_share_returns_404(client, valid_api_key, seeded_history):
    created = (await client.post(
        "/api/portal/chat-shares",
        headers=_headers(valid_api_key),
        json={"conversation_id": CONV_ID, "access_mode": "public",
              "expiry": {"mode": "preset", "preset": "7d"}},
    )).json()["data"]

    async with AsyncSessionLocal() as session:
        row = await svc.get_share(session, created["share_id"])
        row.owner_user_id = "another-user"
        await session.commit()

    resp = await client.post(
        f"/api/portal/chat-shares/{created['share_id']}/revoke",
        headers=_headers(valid_api_key),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/api/portal/test_conversation_shares_api.py -v`
Expected: FAIL — 404（路由不存在）

- [ ] **Step 3: 写端点**

```python
"""会话在线分享（所有者视角）。

只服务登录用户管理自己的分享：创建 / 列表 / 作废。访客读取走免鉴权的
``/api/v1/share/*``。

「查不到」与「不是你的」一律 404：分开返回会让攻击者拿会话 ID 探测别人的数据。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services import conversation_share_service as share_service
from app.services.ai.conversation_identity import require_user_id
from app.services.app_public_url_service import get_app_public_url

logger = logging.getLogger(__name__)

router = APIRouter()


class ExpiryPayload(BaseModel):
    mode: Literal["preset", "custom", "permanent"] = "preset"
    preset: Optional[str] = "7d"
    value: Optional[int] = None
    unit: Optional[Literal["hour", "day"]] = None


class CreateSharePayload(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=50)
    access_mode: Literal["public", "secret"] = "secret"
    secret: Optional[str] = None
    expiry: ExpiryPayload = Field(default_factory=ExpiryPayload)


def _current_user(user_info: Dict[str, Any]) -> tuple[str, str]:
    try:
        user_id = require_user_id(user_info)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="当前用户身份无效") from exc
    username = str(user_info.get("username") or user_info.get("user_name") or "")
    return user_id, username


async def _build_share_url(share_id: str, request: Request) -> str:
    base = await get_app_public_url()
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}/share/{share_id}"


def _serialize(row, secret: Optional[str]) -> Dict[str, Any]:
    return {
        "share_id": row.share_id,
        "title": row.title or "",
        "agent_name": row.agent_name or "",
        "access_mode": row.access_mode,
        "secret": secret,
        "status": share_service.derive_share_status(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "view_count": int(row.view_count or 0),
        "last_viewed_at": row.last_viewed_at.isoformat() if row.last_viewed_at else None,
        "turn_count": int(row.turn_count or 0),
        "message_count": int(row.message_count or 0),
    }


@router.post("", response_model=StandardResponse[Dict[str, Any]], summary="创建会话分享")
async def create_share(
    payload: CreateSharePayload,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, username = _current_user(user_info)
    try:
        expires_at = share_service.resolve_expires_at(payload.expiry.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        row = await share_service.create_share(
            db,
            owner_user_id=user_id,
            owner_username=username,
            conversation_id=payload.conversation_id,
            access_mode=payload.access_mode,
            secret=payload.secret,
            expires_at=expires_at,
        )
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在或不属于当前用户") from exc
    except share_service.ShareContentTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    secret_plain = (
        share_service.decrypt_secret(row.secret_cipher) if row.secret_cipher else None
    )
    data = _serialize(row, secret_plain)
    data["url"] = await _build_share_url(row.share_id, request)
    return StandardResponse(data=data)


@router.get("", response_model=StandardResponse[Dict[str, Any]], summary="我分享的会话链接")
async def list_shares(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _username = _current_user(user_info)
    rows, total = await share_service.list_shares(
        db, user_id=user_id, page=page, page_size=page_size, status=status
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        secret_plain = (
            share_service.decrypt_secret(row.secret_cipher) if row.secret_cipher else None
        )
        item = _serialize(row, secret_plain)
        item["url"] = await _build_share_url(row.share_id, request)
        items.append(item)
    return StandardResponse(data={"total": total, "page": page, "page_size": page_size, "items": items})


@router.post("/{share_id}/revoke", response_model=StandardResponse[Dict[str, Any]], summary="作废分享")
async def revoke_share(
    share_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _username = _current_user(user_info)
    ok = await share_service.revoke_share(db, user_id=user_id, share_id=share_id)
    if not ok:
        raise HTTPException(status_code=404, detail="分享不存在")
    return StandardResponse(data={"revoked": True})
```

- [ ] **Step 4: 注册路由**

`app/api/portal/api.py`：在 `import` 行末尾追加 `chat_shares`，并在「4. 智能体对话」区块下加：

```python
from app.api.portal.endpoints import (..., chat_shares, ...)

# 4.2 会话在线分享（所有者视角）
portal_router.include_router(chat_shares.router, prefix="/chat-shares", tags=["会话分享"], dependencies=[Depends(require_api_key)])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/api/portal/test_conversation_shares_api.py -v`
Expected: 6 passed

---

### Task 6: 访客端点 `/api/v1/share`

**Files:**
- Create: `app/api/v1/endpoints/share.py`
- Modify: `app/api/v1/api.py`
- Test: `tests/api/test_public_share_api.py`

- [ ] **Step 1: 写失败测试**

```python
"""访客端点契约：元信息不含内容、密钥解锁、计数时机、限流、失效统一 404。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, update

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory
from app.services import conversation_share_service as svc
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio

CONV_ID = "conv-share-public-0001"


@pytest.fixture
async def seeded(valid_api_key):
    async with AsyncSessionLocal() as session:
        info = await AuthService.verify_api_key(valid_api_key, session)
        user_id = str(info["user_id"])
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        for index in range(2):
            session.add(AgentExecutionHistory(
                agent_id="agent-public-test", trace_id=f"trace-public-{index}",
                conversation_id=CONV_ID, user_id=user_id, username="tester",
                query=f"问{index}", summary=f"答{index}", status="success",
                created_at=datetime.now() - timedelta(minutes=10 - index),
            ))
        await session.commit()
        row = await svc.create_share(
            session, owner_user_id=user_id, owner_username="tester",
            conversation_id=CONV_ID, access_mode="secret", secret="abcd1234",
            expires_at=datetime.now() + timedelta(days=1),
        )
    yield row.share_id
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentExecutionHistory).where(
            AgentExecutionHistory.conversation_id == CONV_ID
        ))
        await session.execute(delete(svc.ConversationShare).where(
            svc.ConversationShare.conversation_id == CONV_ID
        ))
        await session.commit()


async def test_meta_never_leaks_messages(client, seeded):
    resp = await client.get(f"/api/v1/share/{seeded}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["access_mode"] == "secret"
    assert "messages" not in data and "content_json" not in data
    assert data["owner_display"] == "tester"


async def test_content_requires_token_for_secret_share(client, seeded):
    assert (await client.get(f"/api/v1/share/{seeded}/content")).status_code == 401

    bad = await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "wrongpass"})
    assert bad.status_code == 401

    ok = await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "abcd1234"})
    assert ok.status_code == 200, ok.text
    token = ok.json()["data"]["token"]

    content = await client.get(f"/api/v1/share/{seeded}/content", params={"token": token})
    assert content.status_code == 200, content.text
    messages = content.json()["data"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


async def test_view_count_only_increases_on_content(client, seeded):
    async with AsyncSessionLocal() as session:
        before = await svc.get_share(session, seeded)
        assert before.view_count == 0

    await client.get(f"/api/v1/share/{seeded}")                       # 元信息不计数
    await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "wrongpass"})  # 失败不计数
    async with AsyncSessionLocal() as session:
        mid = await svc.get_share(session, seeded)
        assert mid.view_count == 0

    token = (await client.post(
        f"/api/v1/share/{seeded}/unlock", json={"secret": "abcd1234"}
    )).json()["data"]["token"]
    await client.get(f"/api/v1/share/{seeded}/content", params={"token": token})
    await client.get(f"/api/v1/share/{seeded}/content", params={"token": token})
    async with AsyncSessionLocal() as session:
        after = await svc.get_share(session, seeded)
    assert after.view_count == 2
    assert after.last_viewed_at is not None


async def test_revoked_and_expired_share_return_404(client, seeded):
    async with AsyncSessionLocal() as session:
        await session.execute(update(svc.ConversationShare).where(
            svc.ConversationShare.share_id == seeded
        ).values(status="revoked"))
        await session.commit()
    assert (await client.get(f"/api/v1/share/{seeded}")).status_code == 404
    assert (await client.get(f"/api/v1/share/{seeded}/content")).status_code == 404

    async with AsyncSessionLocal() as session:
        await session.execute(update(svc.ConversationShare).where(
            svc.ConversationShare.share_id == seeded
        ).values(status="active", expires_at=datetime.now() - timedelta(minutes=1)))
        await session.commit()
    assert (await client.get(f"/api/v1/share/{seeded}")).status_code == 404


async def test_unlock_is_rate_limited(client, seeded):
    for _ in range(svc.UNLOCK_FAIL_LIMIT):
        await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "wrongpass"})
    blocked = await client.post(f"/api/v1/share/{seeded}/unlock", json={"secret": "abcd1234"})
    assert blocked.status_code == 429
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/api/test_public_share_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 写公开端点**

```python
"""访客视角的只读分享接口（免鉴权）。

三条路径都挂在 ``public_router``（不经 ``require_api_key``）：
- ``GET /{share_id}``        元信息，不含任何消息内容；
- ``POST /{share_id}/unlock`` 校验访问密码，签发短期令牌；
- ``GET /{share_id}/content`` 返回冻结快照，成功即 +1 访问计数。

失效统一 404：不存在 / 已作废 / 已过期对访客是同一句话，避免被枚举探测。
响应头带 ``X-Robots-Tag``，防止被搜索引擎收录。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services import conversation_share_service as share_service

logger = logging.getLogger(__name__)

public_router = APIRouter()

NOINDEX_HEADERS = {"X-Robots-Tag": "noindex, nofollow"}


class UnlockPayload(BaseModel):
    secret: str


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _optional_viewer_user_id(request: Request, db: AsyncSession) -> Optional[str]:
    """可选鉴权：能认出分享人本人就免密码，认不出就当匿名，绝不因凭据过期把页面打成 401。

    ``public_router`` 不跑 ``require_api_key``，所以这里自己尝试解析一次；
    任何异常都吞掉并降级为匿名——访客页面的第一优先级是「能看」，而不是「严格认证」。
    """
    raw = (
        request.headers.get("x-api-key")
        or (request.headers.get("authorization") or "").replace("Bearer ", "").strip()
        or request.cookies.get("portal_session")
        or request.cookies.get("embed_session")
    )
    if not raw:
        return None
    try:
        from app.services.auth_service import AuthService

        info = await AuthService.verify_api_key(raw, db)
    except Exception:  # noqa: BLE001
        return None
    return str((info or {}).get("user_id") or "") or None
    return None


@public_router.get("/{share_id}", response_model=StandardResponse[Dict[str, Any]], summary="分享元信息")
async def get_share_meta(
    share_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    response.headers.update(NOINDEX_HEADERS)
    try:
        return StandardResponse(data=await share_service.publish_public_meta(db, share_id))
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail="链接不存在或已失效") from exc


@public_router.post("/{share_id}/unlock", response_model=StandardResponse[Dict[str, Any]], summary="解锁分享")
async def unlock_share(
    share_id: str,
    payload: UnlockPayload,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    response.headers.update(NOINDEX_HEADERS)
    try:
        token, expires_in = await share_service.unlock_share(
            db, share_id=share_id, secret=payload.secret, client_ip=_client_ip(request)
        )
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail="链接不存在或已失效") from exc
    except share_service.ShareRateLimitedError as exc:
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试") from exc
    except share_service.ShareAccessDeniedError as exc:
        raise HTTPException(status_code=401, detail="访问密码不正确") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StandardResponse(data={"token": token, "expires_in": expires_in})


@public_router.get("/{share_id}/content", response_model=StandardResponse[Dict[str, Any]], summary="分享内容")
async def get_share_content(
    share_id: str,
    request: Request,
    response: Response,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
):
    response.headers.update(NOINDEX_HEADERS)
    try:
        data = await share_service.read_share_content(
            db,
            share_id=share_id,
            token=token or request.headers.get("x-share-token"),
            viewer_user_id=await _optional_viewer_user_id(request, db),
            client_ip=_client_ip(request),
        )
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail="链接不存在或已失效") from exc
    except share_service.ShareAccessDeniedError as exc:
        raise HTTPException(status_code=401, detail="需要访问密码") from exc
    return StandardResponse(data=data)
```

- [ ] **Step 4: 注册路由**

`app/api/v1/api.py`：追加 import 与挂载

```python
from app.api.v1.endpoints import (..., share, ...)

v1_router.include_router(share.public_router, prefix="/share", tags=["V1 会话分享"])
```

> 注意：`public_router` 必须挂在 `v1_router`（无鉴权依赖），**不要**挂到 `v1_secured`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/api/test_public_share_api.py -v`
Expected: 5 passed

- [ ] **Step 6: 补一条「分享人带登录态免密码」的测试并通过**

追加到同一测试文件：

```python
async def test_owner_with_login_can_read_secret_share_without_token(client, seeded, valid_api_key):
    resp = await client.get(
        f"/api/v1/share/{seeded}/content",
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200, resp.text
```

Run: `pytest tests/api/test_public_share_api.py -v`
Expected: 6 passed

> 该用例覆盖的是 Step 3 里 `_optional_viewer_user_id` 的「本人免密码」路径。
> 若此时失败，先确认 `read_share_content` 判定本人的条件是
> `str(viewer_user_id) == str(row.owner_user_id)`（两侧都统一成字符串，避免 int/str 误判）。

---

### Task 7: 前端纯函数 `shareExpiry.ts` + 契约测试

**Files:**
- Create: `frontend/src/utils/shareExpiry.ts`
- Create: `tests/frontend/share_expiry.mjs`
- Create: `tests/frontend/test_share_expiry_contract.py`

- [ ] **Step 1: 写失败测试（node 直接跑 .ts）**

`tests/frontend/share_expiry.mjs`：

```js
// 用 Node 真跑有效期换算与密码生成：前后端对「7 天」「3 天」这类的换算必须一致，
// 否则会出现「弹窗写着 7 天、列表显示 6 天后过期」。
import assert from 'node:assert/strict'

import {
  EXPIRY_PRESETS,
  deriveShareStatus,
  describeExpiry,
  formatRemaining,
  generateShareSecret,
  resolveExpiry,
} from '../../frontend/src/utils/shareExpiry.ts'

const NOW = new Date('2026-10-28T13:20:00+08:00')

assert.equal(EXPIRY_PRESETS.length, 5)
assert.deepEqual(EXPIRY_PRESETS.map((p) => p.value), ['1h', '24h', '7d', '30d', 'permanent'])
assert.equal(EXPIRY_PRESETS.find((p) => p.value === '7d').label, '7 天')

assert.equal(resolveExpiry({ mode: 'permanent' }, NOW), null)
assert.equal(
  resolveExpiry({ mode: 'preset', preset: '1h' }, NOW).toISOString(),
  new Date('2026-10-28T14:20:00+08:00').toISOString(),
)
assert.equal(
  resolveExpiry({ mode: 'custom', value: 3, unit: 'day' }, NOW).toISOString(),
  new Date('2026-10-31T13:20:00+08:00').toISOString(),
)
assert.equal(
  resolveExpiry({ mode: 'custom', value: 6, unit: 'hour' }, NOW).toISOString(),
  new Date('2026-10-28T19:20:00+08:00').toISOString(),
)

assert.equal(describeExpiry({ mode: 'preset', preset: '7d' }), '7 天后失效')
assert.equal(describeExpiry({ mode: 'permanent' }), '永久有效')
assert.equal(describeExpiry({ mode: 'custom', value: 3, unit: 'day' }), '3 天后失效')

// 密码：8 位、去掉易混字符、每次不同
const seen = new Set()
for (let i = 0; i < 40; i += 1) {
  const secret = generateShareSecret()
  assert.equal(secret.length, 8)
  assert.equal(/[0O1lI]/.test(secret), false, secret)
  assert.equal(/^[a-z0-9]+$/.test(secret), true, secret)
  seen.add(secret)
}
assert.ok(seen.size > 30, '随机源可能被写死')

// 状态推导：作废优先于过期
assert.equal(deriveShareStatus({ status: 'revoked', expires_at: null }, NOW), 'revoked')
assert.equal(
  deriveShareStatus({ status: 'active', expires_at: '2026-10-28T13:00:00+08:00' }, NOW),
  'expired',
)
assert.equal(
  deriveShareStatus({ status: 'active', expires_at: '2026-11-01T13:00:00+08:00' }, NOW),
  'active',
)
assert.equal(deriveShareStatus({ status: 'active', expires_at: null }, NOW), 'active')

assert.equal(formatRemaining(null, NOW), '永久有效')
assert.equal(formatRemaining('2026-10-30T13:20:00+08:00', NOW), '2 天后过期')
assert.equal(formatRemaining('2026-10-28T18:20:00+08:00', NOW), '5 小时后过期')
assert.equal(formatRemaining('2026-10-28T13:10:00+08:00', NOW), '已过期')

console.log('会话分享有效期测试全部通过')
```

`tests/frontend/test_share_expiry_contract.py`：

```python
"""分享有效期/密码纯函数的行为测试（node 真跑 TS）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行行为测试")
def test_share_expiry_behaviour():
    script = Path("tests/frontend/share_expiry.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "会话分享有效期测试全部通过" in proc.stdout
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_share_expiry_contract.py -v`
Expected: FAIL — `Cannot find module ... shareExpiry.ts`

- [ ] **Step 3: 实现**

```ts
/**
 * 会话分享的纯函数：有效期换算、密码生成、状态推导、剩余时间文案。
 *
 * 抽成纯函数而不是写在组件里，是为了让 node 能直接跑行为测试——这几个换算前后端
 * 必须一致，错一天用户就会看到「弹窗写 7 天、列表显示 6 天后过期」。
 */

export type ExpiryPresetValue = '1h' | '24h' | '7d' | '30d' | 'permanent'

export interface ExpiryChoice {
  mode: 'preset' | 'custom' | 'permanent'
  preset?: ExpiryPresetValue
  value?: number
  unit?: 'hour' | 'day'
}

export interface ExpiryPreset {
  value: ExpiryPresetValue
  label: string
}

export type ShareStatus = 'active' | 'expired' | 'revoked'

export const EXPIRY_PRESETS: ExpiryPreset[] = [
  { value: '1h', label: '1 小时' },
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
  { value: 'permanent', label: '永久' },
]

const PRESET_MS: Record<Exclude<ExpiryPresetValue, 'permanent'>, number> = {
  '1h': 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
}

/** 与服务端 `SECRET_ALPHABET` 完全一致：去掉 0/O/1/l/I。 */
export const SHARE_SECRET_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
export const SHARE_SECRET_LENGTH = 8

export function resolveExpiry(choice: ExpiryChoice, now: Date = new Date()): Date | null {
  if (choice.mode === 'permanent') return null
  if (choice.mode === 'custom') {
    const value = Number(choice.value)
    if (!Number.isFinite(value) || value <= 0) return null
    const unitMs = choice.unit === 'hour' ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000
    return new Date(now.getTime() + value * unitMs)
  }
  const preset = (choice.preset || '7d') as ExpiryPresetValue
  if (preset === 'permanent') return null
  const delta = PRESET_MS[preset]
  return delta ? new Date(now.getTime() + delta) : null
}

export function describeExpiry(choice: ExpiryChoice): string {
  if (choice.mode === 'permanent') return '永久有效'
  if (choice.mode === 'custom') {
    const value = Number(choice.value)
    return `${value} ${choice.unit === 'hour' ? '小时后' : '天后'}失效`
  }
  const preset = EXPIRY_PRESETS.find((item) => item.value === (choice.preset || '7d'))
  if (!preset) return '7 天后失效'
  if (preset.value === 'permanent') return '永久有效'
  return `${preset.label}后失效`
}

export function generateShareSecret(
  length: number = SHARE_SECRET_LENGTH,
  random: () => number = Math.random,
): string {
  let out = ''
  for (let i = 0; i < length; i += 1) {
    const index = Math.floor(random() * SHARE_SECRET_ALPHABET.length) % SHARE_SECRET_ALPHABET.length
    out += SHARE_SECRET_ALPHABET[index]
  }
  return out
}

export function deriveShareStatus(
  row: { status?: string | null; expires_at?: string | null },
  now: Date = new Date(),
): ShareStatus {
  if (String(row?.status || '') === 'revoked') return 'revoked'
  const expiresAt = row?.expires_at ? new Date(row.expires_at) : null
  if (expiresAt && !Number.isNaN(expiresAt.getTime()) && expiresAt.getTime() <= now.getTime()) {
    return 'expired'
  }
  return 'active'
}

export function formatRemaining(expiresAt: string | null | undefined, now: Date = new Date()): string {
  if (!expiresAt) return '永久有效'
  const target = new Date(expiresAt)
  if (Number.isNaN(target.getTime())) return '永久有效'
  const diff = target.getTime() - now.getTime()
  if (diff <= 0) return '已过期'
  const minutes = Math.floor(diff / 60000)
  if (minutes < 60) return `${Math.max(1, minutes)} 分钟后过期`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时后过期`
  return `${Math.floor(hours / 24)} 天后过期`
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_share_expiry_contract.py -v`
Expected: 1 passed

---

### Task 8: 前端 API 层 `chatShare.ts`

**Files:**
- Create: `frontend/src/api/chatShare.ts`
- Test: `tests/frontend/test_chat_share_api_contract.py`

- [ ] **Step 1: 写失败测试（源码契约）**

```python
"""前端分享 API 层的接线契约：端点、鉴权头、错误分支。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
SOURCE = "frontend/src/api/chatShare.ts"


def _source() -> str:
    return (ROOT / SOURCE).read_text(encoding="utf-8")


def test_owner_endpoints_use_portal_prefix():
    source = _source()
    assert "/api/portal/chat-shares" in source
    assert "revoke" in source
    # 创建 / 列表 / 作废三条都要带鉴权头注入点
    assert "headers" in source


def test_visitor_endpoints_are_public_and_take_token():
    source = _source()
    assert "/api/v1/share/" in source
    assert "unlock" in source
    assert "content" in source
    assert "X-Share-Token" in source


def test_types_mirror_backend_payload():
    source = _source()
    for field in (
        "access_mode", "expiry", "secret", "share_id", "view_count",
        "last_viewed_at", "expires_at", "turn_count",
    ):
        assert field in source, field
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_chat_share_api_contract.py -v`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 实现**

```ts
/**
 * 会话在线分享的 HTTP 层。
 *
 * 所有者接口走 `/api/portal/chat-shares`（门户 Cookie 由 axios 实例自动携带；
 * 嵌入场景由调用方传入 Bearer/X-API-Key）；访客接口走 `/api/v1/share/*`（免鉴权）。
 */
import axios from 'axios'

export interface ShareExpiryPayload {
  mode: 'preset' | 'custom' | 'permanent'
  preset?: string
  value?: number
  unit?: 'hour' | 'day'
}

export interface CreateSharePayload {
  conversation_id: string
  access_mode: 'public' | 'secret'
  secret?: string
  expiry: ShareExpiryPayload
}

export interface ShareListItem {
  share_id: string
  url: string
  title: string
  agent_name: string
  access_mode: 'public' | 'secret'
  secret: string | null
  status: 'active' | 'expired' | 'revoked'
  expires_at: string | null
  created_at: string | null
  revoked_at: string | null
  view_count: number
  last_viewed_at: string | null
  turn_count: number
  message_count: number
}

export interface ShareListResponse {
  total: number
  page: number
  page_size: number
  items: ShareListItem[]
}

export interface ShareMeta {
  title: string
  agent_name: string
  access_mode: 'public' | 'secret'
  status: 'active' | 'expired' | 'revoked'
  expires_at: string | null
  remaining_seconds: number | null
  message_count: number
  turn_count: number
  created_at: string | null
  owner_display: string
}

export interface ShareMessage {
  role: 'user' | 'assistant'
  content: string
  created_at: string | null
}

export interface ShareContent {
  title: string
  agent_name: string
  created_at: string | null
  messages: ShareMessage[]
}

export type AuthHeaders = () => Record<string, string> | undefined

function ownerHeaders(getHeaders?: AuthHeaders): Record<string, string> {
  return { ...(getHeaders?.() || {}) }
}

export async function createConversationShare(
  payload: CreateSharePayload,
  getHeaders?: AuthHeaders,
): Promise<ShareListItem> {
  const resp = await axios.post('/api/portal/chat-shares', payload, {
    headers: ownerHeaders(getHeaders),
  })
  return resp.data?.data
}

export async function listConversationShares(
  params: { page?: number; page_size?: number; status?: string } = {},
  getHeaders?: AuthHeaders,
): Promise<ShareListResponse> {
  const resp = await axios.get('/api/portal/chat-shares', {
    params,
    headers: ownerHeaders(getHeaders),
  })
  return resp.data?.data
}

export async function revokeConversationShare(
  shareId: string,
  getHeaders?: AuthHeaders,
): Promise<boolean> {
  const resp = await axios.post(
    `/api/portal/chat-shares/${encodeURIComponent(shareId)}/revoke`,
    {},
    { headers: ownerHeaders(getHeaders) },
  )
  return Boolean(resp.data?.data?.revoked)
}

export async function fetchShareMeta(shareId: string): Promise<ShareMeta> {
  const resp = await axios.get(`/api/v1/share/${encodeURIComponent(shareId)}`)
  return resp.data?.data
}

export async function unlockShare(
  shareId: string,
  secret: string,
): Promise<{ token: string; expires_in: number }> {
  const resp = await axios.post(`/api/v1/share/${encodeURIComponent(shareId)}/unlock`, { secret })
  return resp.data?.data
}

export async function fetchShareContent(shareId: string, token?: string): Promise<ShareContent> {
  const resp = await axios.get(`/api/v1/share/${encodeURIComponent(shareId)}/content`, {
    // 令牌走请求头而不是 query：避免被复制进浏览器历史、Referer 与访问日志
    headers: token ? { 'X-Share-Token': token } : {},
  })
  return resp.data?.data
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_chat_share_api_contract.py -v`
Expected: 3 passed


---

### Task 9: 创建分享弹窗 `ShareConversationDialog.vue`

**Files:**
- Create: `frontend/src/components/ShareConversationDialog.vue`
- Test: `tests/frontend/test_share_dialog_contract.py`

视觉基准：`docs/mockups/2026-10-28-conversation-share.html` 的场景 1 与场景 2 右侧。

- [ ] **Step 1: 写失败测试（源码契约）**

```python
"""弹窗接线契约：有效期选项、密码可改、结果态分开复制、错误不吞。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
SOURCE = "frontend/src/components/ShareConversationDialog.vue"


def _source() -> str:
    return (ROOT / SOURCE).read_text(encoding="utf-8")


def test_uses_shared_expiry_helpers_instead_of_inline_math():
    source = _source()
    assert "resolveExpiry" in source
    assert "describeExpiry" in source
    assert "generateShareSecret" in source


def test_both_access_modes_and_custom_expiry_are_offered():
    source = _source()
    assert "公开链接" in source and "密钥访问" in source
    assert "24 小时" in source and "永久" in source
    assert "自定义" in source


def test_result_state_exposes_link_and_secret_separately():
    source = _source()
    assert "复制链接" in source and "复制密码" in source
    assert "打开预览" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_share_dialog_contract.py -v`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 实现组件**

```vue
<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px]"
      @click.self="close"
    >
      <div class="w-full max-w-[480px] overflow-hidden rounded-2xl border border-white/60 bg-white shadow-2xl">
        <!-- 结果态 -->
        <template v-if="result">
          <div class="px-5 pt-6 pb-2 text-center">
            <div class="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full bg-emerald-500/10 text-emerald-600">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6">
                <path d="M4.5 12.5l5 5 10-11" />
              </svg>
            </div>
            <h3 class="text-[15px] font-extrabold text-gray-900">分享已创建</h3>
            <p class="mt-1.5 text-[11.5px] text-gray-400">
              把链接和密码一起发给对方，对方打开链接后输入密码即可查看
            </p>
          </div>

          <div class="px-5 pb-2">
            <div class="mt-2 flex items-center gap-2 rounded-[10px] border border-gray-200 bg-gray-50/70 px-2.5 py-2">
              <span class="w-11 shrink-0 text-[10px] font-extrabold tracking-wider text-gray-400">链接</span>
              <span class="min-w-0 flex-1 truncate text-[11.5px] text-gray-700">{{ result.url }}</span>
              <button class="shrink-0 rounded-md border border-primary/30 px-2 py-1 text-[11px] font-bold text-primary" @click="copy(result.url)">
                {{ copied === 'url' ? '已复制' : '复制' }}
              </button>
            </div>
            <div v-if="result.secret" class="mt-2 flex items-center gap-2 rounded-[10px] border border-gray-200 bg-gray-50/70 px-2.5 py-2">
              <span class="w-11 shrink-0 text-[10px] font-extrabold tracking-wider text-gray-400">密码</span>
              <span class="min-w-0 flex-1 truncate font-mono text-[12px] font-bold tracking-[0.16em] text-gray-800">{{ result.secret }}</span>
              <button class="shrink-0 rounded-md border border-primary/30 px-2 py-1 text-[11px] font-bold text-primary" @click="copy(result.secret, 'secret')">
                {{ copied === 'secret' ? '已复制' : '复制' }}
              </button>
            </div>
          </div>

          <div class="flex justify-end gap-2 border-t border-gray-100 bg-slate-50/60 px-5 py-3.5">
            <button class="rounded-lg px-3 py-2 text-[12.5px] font-semibold text-gray-500 hover:bg-gray-100" @click="openPreview">打开预览</button>
            <button class="rounded-lg bg-primary px-4 py-2 text-[12.5px] font-semibold text-white" @click="close">完成</button>
          </div>
        </template>

        <!-- 配置态 -->
        <template v-else>
          <div class="flex items-start gap-2.5 px-5 pt-4 pb-3">
            <div class="min-w-0">
              <h3 class="text-[15px] font-extrabold text-gray-900">分享会话</h3>
              <p class="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-gray-400">{{ conversationTitle }}</p>
            </div>
            <button class="ml-auto text-gray-400 hover:text-gray-600" @click="close">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>

          <div class="px-5 pb-1">
            <div class="mt-1 text-[11.5px] font-bold text-gray-700">访问方式</div>
            <div class="mt-2 grid grid-cols-2 gap-2">
              <button
                v-for="option in ACCESS_OPTIONS"
                :key="option.value"
                class="rounded-[11px] border px-2.5 py-2.5 text-left transition-colors"
                :class="accessMode === option.value
                  ? 'border-primary bg-primary/5 ring-1 ring-primary/25'
                  : 'border-gray-200 hover:border-gray-300'"
                @click="accessMode = option.value"
              >
                <span class="flex items-center gap-1.5 text-[12.5px] font-bold text-gray-800">
                  <span class="grid h-3.5 w-3.5 place-items-center rounded-full border"
                        :class="accessMode === option.value ? 'border-primary' : 'border-gray-300'">
                    <span v-if="accessMode === option.value" class="h-1.5 w-1.5 rounded-full bg-primary"></span>
                  </span>
                  {{ option.label }}
                </span>
                <span class="mt-1 block text-[10.5px] leading-snug text-gray-400">{{ option.desc }}</span>
              </button>
            </div>

            <template v-if="accessMode === 'secret'">
              <div class="mt-3 flex items-center gap-1.5 text-[11.5px] font-bold text-gray-700">
                访问密码
                <span class="font-medium text-gray-400">可自动生成，也可以自己改</span>
              </div>
              <div class="mt-2 flex gap-2">
                <input
                  v-model="secret"
                  maxlength="32"
                  class="h-9 flex-1 rounded-[9px] border border-gray-200 px-2.5 font-mono text-[13px] tracking-[0.14em] text-gray-800 outline-none focus:border-primary"
                />
                <button class="h-9 shrink-0 rounded-lg border border-gray-200 px-3 text-[12px] font-semibold text-gray-600" @click="secret = generateShareSecret()">
                  换一个
                </button>
              </div>
            </template>

            <div class="mt-3 text-[11.5px] font-bold text-gray-700">有效期</div>
            <div class="mt-2 flex flex-wrap gap-1.5">
              <button
                v-for="preset in EXPIRY_PRESETS"
                :key="preset.value"
                class="h-[30px] rounded-lg border px-2.5 text-[12px] font-semibold"
                :class="isPresetActive(preset.value)
                  ? 'border-primary bg-primary text-white'
                  : 'border-gray-200 text-gray-700 hover:border-gray-300'"
                @click="selectPreset(preset.value)"
              >
                {{ preset.label }}
              </button>
            </div>
            <div class="mt-2 flex items-center gap-1.5">
              <input
                v-model.number="customValue"
                type="number"
                min="1"
                placeholder="自定义"
                class="h-8 w-[76px] rounded-lg border border-gray-200 px-2 text-[12.5px] outline-none focus:border-primary"
                @focus="expiryMode = 'custom'"
              />
              <select
                v-model="customUnit"
                class="h-8 rounded-lg border border-gray-200 px-2 text-[12.5px] outline-none focus:border-primary"
                @focus="expiryMode = 'custom'"
                @change="expiryMode = 'custom'"
              >
                <option value="hour">小时</option>
                <option value="day">天</option>
              </select>
            </div>

            <div class="mt-3 flex items-start gap-1.5 rounded-[9px] border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-0.5 shrink-0">
                <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.5" />
              </svg>
              <span>
                只包含已完成的 <b>{{ turnCount }}</b> 轮问答{{ running ? '（当前仍在生成，后续轮次不会带出）' : '' }}；
                思考过程、工具调用与生成的文件不会随分享带出。
              </span>
            </div>

            <p v-if="existingActiveCount > 0" class="mt-2 text-[10.5px] text-gray-400">
              该会话已有 {{ existingActiveCount }} 条生效中的分享，本次会创建一条新的独立链接。
            </p>
            <p v-if="errorMessage" class="mt-2 text-[11px] text-red-500">{{ errorMessage }}</p>
          </div>

          <div class="mt-3 flex items-center gap-2 border-t border-gray-100 bg-slate-50/60 px-5 py-3.5">
            <span class="text-[10.5px] text-gray-400">{{ expiryHint }}</span>
            <div class="ml-auto flex gap-2">
              <button class="rounded-lg px-3 py-2 text-[12.5px] font-semibold text-gray-500 hover:bg-gray-100" @click="close">取消</button>
              <button
                class="rounded-lg bg-primary px-4 py-2 text-[12.5px] font-semibold text-white disabled:opacity-60"
                :disabled="submitting"
                @click="submit"
              >
                {{ submitting ? '创建中…' : '创建分享' }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  EXPIRY_PRESETS,
  describeExpiry,
  generateShareSecret,
  resolveExpiry,
  type ExpiryChoice,
  type ExpiryPresetValue,
} from '@/utils/shareExpiry'
import {
  createConversationShare,
  type AuthHeaders,
  type ShareListItem,
  type ShareExpiryPayload,
} from '@/api/chatShare'

const props = withDefaults(
  defineProps<{
    visible: boolean
    conversationId: string
    conversationTitle?: string
    turnCount?: number
    running?: boolean
    existingActiveCount?: number
    authHeaders?: AuthHeaders
  }>(),
  {
    conversationTitle: '',
    turnCount: 0,
    running: false,
    existingActiveCount: 0,
    authHeaders: undefined,
  },
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'created', item: ShareListItem): void
  (e: 'toast', message: string, type: 'success' | 'error' | 'info' | 'warning'): void
}>()

const ACCESS_OPTIONS = [
  { value: 'public' as const, label: '公开链接', desc: '拿到链接的人都能直接查看' },
  { value: 'secret' as const, label: '密钥访问', desc: '需要输入访问密码才能查看' },
]

const accessMode = ref<'public' | 'secret'>('secret')
const secret = ref(generateShareSecret())
const expiryMode = ref<'preset' | 'custom' | 'permanent'>('preset')
const presetValue = ref<ExpiryPresetValue>('7d')
const customValue = ref<number | null>(null)
const customUnit = ref<'hour' | 'day'>('day')
const submitting = ref(false)
const errorMessage = ref('')
const result = ref<ShareListItem | null>(null)
const copied = ref<'' | 'url' | 'secret'>('')

const expiryChoice = computed<ExpiryChoice>(() => {
  if (expiryMode.value === 'permanent') return { mode: 'permanent' }
  if (expiryMode.value === 'custom') {
    return { mode: 'custom', value: customValue.value || undefined, unit: customUnit.value }
  }
  return { mode: 'preset', preset: presetValue.value }
})

const expiryHint = computed(() => {
  if (expiryMode.value === 'custom' && !customValue.value) return '请填写自定义有效期'
  const target = resolveExpiry(expiryChoice.value)
  if (!target) return '永久有效'
  return `${target.toLocaleString()} 自动失效`
})

function isPresetActive(value: ExpiryPresetValue): boolean {
  return expiryMode.value === 'preset' && presetValue.value === value
}

function selectPreset(value: ExpiryPresetValue): void {
  if (value === 'permanent') {
    expiryMode.value = 'permanent'
    return
  }
  expiryMode.value = 'preset'
  presetValue.value = value
}

function reset(): void {
  accessMode.value = 'secret'
  secret.value = generateShareSecret()
  expiryMode.value = 'preset'
  presetValue.value = '7d'
  customValue.value = null
  customUnit.value = 'day'
  submitting.value = false
  errorMessage.value = ''
  result.value = null
  copied.value = ''
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) reset()
  },
)

function close(): void {
  emit('close')
}

async function copy(text: string, kind: 'url' | 'secret' = 'url'): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = kind
    emit('toast', '已复制到剪贴板', 'success')
    window.setTimeout(() => { copied.value = '' }, 1800)
  } catch {
    emit('toast', '复制失败，请手动选中复制', 'error')
  }
}

function openPreview(): void {
  if (!result.value) return
  window.open(result.value.url, '_blank', 'noopener')
}

async function submit(): Promise<void> {
  if (!props.conversationId) {
    errorMessage.value = '当前会话还不支持分享'
    return
  }
  if (accessMode.value === 'secret' && secret.value.trim().length < 6) {
    errorMessage.value = '访问密码至少 6 位'
    return
  }
  if (expiryMode.value === 'custom' && !customValue.value) {
    errorMessage.value = '请填写自定义有效期'
    return
  }

  const expiry: ShareExpiryPayload =
    expiryMode.value === 'preset'
      ? { mode: 'preset', preset: presetValue.value }
      : expiryMode.value === 'permanent'
        ? { mode: 'permanent' }
        : { mode: 'custom', value: Number(customValue.value), unit: customUnit.value }

  submitting.value = true
  errorMessage.value = ''
  try {
    const created = await createConversationShare(
      {
        conversation_id: props.conversationId,
        access_mode: accessMode.value,
        secret: accessMode.value === 'secret' ? secret.value.trim() : undefined,
        expiry,
      },
      props.authHeaders,
    )
    result.value = created
    emit('created', created)
  } catch (error: any) {
    errorMessage.value = String(error?.response?.data?.detail || '创建分享失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>
```

> 说明：`describeExpiry` 在本组件只用于 `expiryHint` 的兜底文案；若实现时发现未使用，删掉该 import，否则 `vue-tsc` 会报未使用变量。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_share_dialog_contract.py -v`
Expected: 3 passed

---

### Task 10: 「我分享的」面板 + 侧栏 tab

**Files:**
- Create: `frontend/src/components/SharedLinksPanel.vue`
- Modify: `frontend/src/components/ChatHistorySidebar.vue`
- Test: `tests/frontend/test_shared_links_panel_contract.py`

- [ ] **Step 1: 写失败测试**

```python
"""分享面板与侧栏 tab 的接线契约。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_exposes_copy_revoke_and_stats():
    source = _source("frontend/src/components/SharedLinksPanel.vue")
    assert "复制链接" in source and "复制密码" in source
    assert "作废" in source and "确认作废" in source
    assert "view_count" in source and "last_viewed_at" in source
    assert "state" in source  # active/expired/revoked 文案


def test_sidebar_has_two_tabs_and_share_button():
    source = _source("frontend/src/components/ChatHistorySidebar.vue")
    assert "会话历史" in source and "我分享的" in source
    assert 'emit("share-chat", item)' in source
    assert "enableShare" in source
    # 搜索与筛选只在历史 tab 内渲染
    assert 'v-if="activeTab === \'history\'"' in source


def test_sidebar_does_not_fetch_by_itself():
    """侧栏只 emit，请求由宿主注入的 headers 在面板内发起，避免两处各写一套鉴权。"""
    source = _source("frontend/src/components/ChatHistorySidebar.vue")
    assert "axios" not in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_shared_links_panel_contract.py -v`
Expected: FAIL

- [ ] **Step 3: 写面板组件**

```vue
<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex-1 overflow-y-auto px-2.5 py-3">
      <div v-if="loading && items.length === 0" class="space-y-2">
        <div v-for="n in 3" :key="n" class="h-[92px] animate-pulse rounded-xl bg-gray-100 dark:bg-gray-800"></div>
      </div>

      <div v-else-if="errorMessage" class="px-2 py-8 text-center">
        <p class="text-[11.5px] text-red-500">{{ errorMessage }}</p>
        <button class="mt-2 text-[11.5px] font-semibold text-primary" @click="load()">重试</button>
      </div>

      <div v-else-if="items.length === 0" class="px-4 py-10 text-center">
        <div class="mx-auto mb-2 grid h-10 w-10 place-items-center rounded-xl bg-gray-100 text-gray-400 dark:bg-gray-800">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M10 13a4.5 4.5 0 006.4 0l2.2-2.2a4.5 4.5 0 00-6.4-6.4L10.9 5.7" />
            <path d="M14 11a4.5 4.5 0 00-6.4 0l-2.2 2.2a4.5 4.5 0 006.4 6.4l1.3-1.3" />
          </svg>
        </div>
        <p class="text-[12px] font-semibold text-gray-500">还没有分享过会话</p>
        <p class="mt-1 text-[11px] leading-relaxed text-gray-400">
          在「会话历史」里点条目上的分享图标，<br />可选公开或密钥访问、设置有效期。
        </p>
      </div>

      <div v-else class="space-y-2">
        <div
          v-for="item in items"
          :key="item.share_id"
          class="rounded-xl border bg-white p-3 dark:bg-gray-800/80"
          :class="item.status === 'active' ? 'border-gray-100 dark:border-gray-800' : 'border-dashed border-gray-200 opacity-80'"
        >
          <p class="line-clamp-2 text-[12.5px] font-bold leading-snug text-gray-800 dark:text-gray-100"
             :class="{ 'text-gray-400 line-through dark:text-gray-500': item.status !== 'active' }">
            {{ item.title || '未命名会话' }}
          </p>

          <div class="mt-1.5 flex flex-wrap items-center gap-1">
            <span class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500 dark:bg-gray-700 dark:text-gray-300">
              {{ item.access_mode === 'secret' ? '🔑 密钥' : '🌐 公开' }}
            </span>
            <span class="rounded-md px-1.5 py-0.5 text-[10px] font-bold" :class="statusClass(item.status)">
              {{ statusLabel(item.status) }}
            </span>
          </div>

          <p class="mt-1.5 text-[10.5px] text-gray-500 dark:text-gray-400">
            已打开 <b class="text-gray-700 dark:text-gray-200">{{ item.view_count }}</b> 次
            <template v-if="item.last_viewed_at"> · 最近 {{ relativeTime(item.last_viewed_at) }}</template>
          </p>
          <p class="mt-0.5 text-[10.5px] text-gray-400">
            {{ expiryText(item) }} · {{ formatTime(item.created_at) }} 创建
          </p>

          <div v-if="confirmingId === item.share_id" class="mt-2 flex items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5">
            <span class="text-[11px] font-bold text-red-600">作废后链接立即失效</span>
            <span class="flex gap-1.5">
              <button class="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-semibold text-gray-600" @click="confirmingId = ''">取消</button>
              <button class="rounded-md border border-red-200 bg-white px-2 py-1 text-[11px] font-bold text-red-600" @click="revoke(item.share_id)">确认作废</button>
            </span>
          </div>

          <div v-else class="mt-2 flex gap-1.5">
            <button
              class="rounded-md border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300"
              :disabled="item.status !== 'active'"
              @click="copy(item.url, '链接')"
            >复制链接</button>
            <button
              v-if="item.access_mode === 'secret'"
              class="rounded-md border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300"
              :disabled="item.status !== 'active' || !item.secret"
              @click="copy(item.secret || '', '密码')"
            >复制密码</button>
            <button
              v-if="item.status === 'active'"
              class="rounded-md border border-red-200 bg-red-50/60 px-2 py-1 text-[11px] font-bold text-red-600 dark:border-red-900/50 dark:bg-red-950/30"
              @click="confirmingId = item.share_id"
            >作废</button>
          </div>
        </div>

        <div v-if="hasMore" class="pt-1 text-center">
          <button class="text-[11.5px] font-semibold text-primary" :disabled="loading" @click="loadMore">
            {{ loading ? '加载中…' : '加载更多' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  listConversationShares,
  revokeConversationShare,
  type AuthHeaders,
  type ShareListItem,
} from '@/api/chatShare'
import { deriveShareStatus, formatRemaining } from '@/utils/shareExpiry'

const props = withDefaults(
  defineProps<{ authHeaders?: AuthHeaders }>(),
  { authHeaders: undefined },
)

const emit = defineEmits<{
  (e: 'toast', message: string, type: 'success' | 'error' | 'info' | 'warning'): void
}>()

const PAGE_SIZE = 20

const items = ref<ShareListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const errorMessage = ref('')
const confirmingId = ref('')

const hasMore = ref(false)

function statusLabel(status: string): string {
  if (status === 'revoked') return '已作废'
  if (status === 'expired') return '已过期'
  return '生效中'
}

function statusClass(status: string): string {
  if (status === 'revoked') return 'bg-gray-100 text-gray-400'
  if (status === 'expired') return 'bg-amber-50 text-amber-700'
  return 'bg-emerald-50 text-emerald-700'
}

function expiryText(item: ShareListItem): string {
  if (!item.expires_at) return '永久有效'
  if (deriveShareStatus(item) !== 'active') return `${formatTime(item.expires_at)} 失效`
  return formatRemaining(item.expires_at)
}

function formatTime(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

function relativeTime(value: string): string {
  const date = new Date(value)
  const diff = Date.now() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

async function load(reset = true): Promise<void> {
  if (loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const target = reset ? 1 : page.value + 1
    const data = await listConversationShares(
      { page: target, page_size: PAGE_SIZE },
      props.authHeaders,
    )
    const incoming = data?.items || []
    items.value = reset ? incoming : [...items.value, ...incoming]
    total.value = Number(data?.total || 0)
    page.value = target
    hasMore.value = items.value.length < total.value
  } catch (error: any) {
    errorMessage.value = String(error?.response?.data?.detail || '加载分享列表失败')
  } finally {
    loading.value = false
  }
}

function loadMore(): void {
  void load(false)
}

async function copy(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    emit('toast', `${label}已复制`, 'success')
  } catch {
    emit('toast', '复制失败，请手动选中复制', 'error')
  }
}

async function revoke(shareId: string): Promise<void> {
  confirmingId.value = ''
  try {
    await revokeConversationShare(shareId, props.authHeaders)
    const target = items.value.find((item) => item.share_id === shareId)
    if (target) {
      target.status = 'revoked'
      target.revoked_at = new Date().toISOString()
    }
    emit('toast', '分享已作废', 'success')
  } catch (error: any) {
    emit('toast', String(error?.response?.data?.detail || '作废失败'), 'error')
  }
}

onMounted(() => {
  void load()
})

defineExpose({ reload: () => load() })
</script>
```

- [ ] **Step 4: 改侧栏：tab + 分享按钮 + `enableShare`**

`ChatHistorySidebar.vue` 按下列 6 处修改（顺序执行）：

1）`defineProps` 里追加：

```ts
    /** 是否显示条目上的「分享」按钮：只有主聊天页打开，调试页不显示。 */
    enableShare?: boolean;
    /** 注入给「我分享的」面板的鉴权头（侧栏自己不发请求）。 */
    shareAuthHeaders?: () => Record<string, string> | undefined;
```

默认值块追加：

```ts
    enableShare: false,
    shareAuthHeaders: undefined,
```

2）`defineEmits` 追加：

```ts
  (e: "share-chat", item: any): void;
```

3）`<script setup>` 里（`const windowWidth = ref(...)` 之前）加：

```ts
import SharedLinksPanel from "@/components/SharedLinksPanel.vue";

/** 侧栏两个视图：历史列表 / 我发出的分享。 */
const activeTab = ref<"history" | "shares">("history");
const sharesPanelRef = ref<InstanceType<typeof SharedLinksPanel> | null>(null);

const switchTab = (tab: "history" | "shares") => {
  if (activeTab.value === tab) return;
  activeTab.value = tab;
  if (tab === "shares") sharesPanelRef.value?.reload?.();
};
```

4）标题栏下方插入 tab 行（放在 `.sb-top` 之后、搜索框之前）：

```html
      <div class="flex gap-1 px-3.5 pt-2.5">
        <button
          type="button"
          class="rounded-lg px-2.5 py-1.5 text-[12.5px] font-semibold transition-colors"
          :class="activeTab === 'history'
            ? 'bg-primary/10 text-primary'
            : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'"
          @click="switchTab('history')"
        >会话历史</button>
        <button
          type="button"
          class="rounded-lg px-2.5 py-1.5 text-[12.5px] font-semibold transition-colors"
          :class="activeTab === 'shares'
            ? 'bg-primary/10 text-primary'
            : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800'"
          @click="switchTab('shares')"
        >我分享的</button>
      </div>
```

5）搜索框整块包一层 `v-if="activeTab === 'history'"`（搜索框 + 筛选按钮 + 筛选面板都只属于历史 tab）。即把原来那个 `<div class="...">` 加上 `v-if`。

6）历史列表容器与面板切换：给原来的列表外层 `<div v-else class="space-y-3 p-2.5">` 加上 `v-if="activeTab === 'history'"`，并在其后插入：

```html
      <SharedLinksPanel
        v-else
        ref="sharesPanelRef"
        :auth-headers="shareAuthHeaders"
        @toast="(message: string, type: any) => emit('toast', message, type)"
      />
```

> 侧栏原本没有 `toast` emit。追加 `(e: "toast", message: string, type: string): void;`，并在步骤 2 的 emit 列表里一起加。

7）条目操作区加分享按钮（放在「导出」与「回溯日志」之间）：

```html
                <button
                  v-if="enableShare && (item.conversation_id || item.trace_id)"
                  @click.stop="emit('share-chat', item)"
                  class="p-1 rounded-md text-gray-400 hover:text-primary hover:bg-primary/10 transition-all"
                  :class="actionVisibilityClass"
                  title="分享会话"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.1">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M10 13a4.5 4.5 0 006.4 0l2.2-2.2a4.5 4.5 0 00-6.4-6.4L10.9 5.7" />
                    <path stroke-linecap="round" stroke-linejoin="round" d="M14 11a4.5 4.5 0 00-6.4 0l-2.2 2.2a4.5 4.5 0 006.4 6.4l1.3-1.3" />
                  </svg>
                </button>
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_shared_links_panel_contract.py -v`
Expected: 3 passed

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无新增错误（若侧栏原有的 emit 类型未含 toast，按步骤 4 补齐）


---

### Task 11: `MessageRenderer` 只读降级

**Files:**
- Modify: `frontend/src/components/MessageRenderer.vue`
- Test: `tests/frontend/test_message_renderer_readonly_contract.py`

只读模式要做的事其实只有两件：**渲染前**不生成 citation 角标，**渲染后**不再做路径重映射与产物增强。Markdown / ECharts / Mermaid / 表格全部保持原样——分享页的价值就在这里。

之所以用「提前 return」而不是给每处 replace 加 `if`：`postProcessHtml` 后段（路径重映射 → 占位符还原 → 产物下载按钮 → 角标兜底 → 路径注入）是一条为聊天页设计的流水线，分享页一个都不需要；逐点打补丁容易漏掉后加的分支。

- [ ] **Step 1: 写失败测试**

```python
"""只读渲染契约：分享页复用同一渲染内核，但必须摘掉需要登录态的增强。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
SOURCE = "frontend/src/components/MessageRenderer.vue"


def _source() -> str:
    return (ROOT / SOURCE).read_text(encoding="utf-8")


def test_readonly_prop_exists_and_defaults_to_false():
    source = _source()
    assert "readonly?: boolean" in source
    assert "readonly: false" in source


def test_readonly_short_circuits_login_dependent_processing():
    source = _source()
    # 两处短路：渲染前的 citation 保护、渲染后的路径重映射/下载按钮/角标兜底
    assert source.count("props.readonly") >= 2
    assert "if (props.readonly) {" in source
    assert "return res;" in source


def test_canvas_cards_degrade_in_readonly_mode():
    source = _source()
    assert source.count('v-if="readonly"') >= 2, "两块画布卡片都要降级为源码"
    assert "segment.content" in source


def test_public_share_page_uses_readonly_renderer():
    source = (ROOT / "frontend/src/views/SharedConversation.vue").read_text(encoding="utf-8")
    assert "MessageRenderer" in source
    assert "readonly" in source
    assert "hide-quick-buttons" in source or "hideQuickButtons" in source
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_message_renderer_readonly_contract.py -v`
Expected: 前 3 条 FAIL（`props.readonly` 尚不存在）

- [ ] **Step 3: 加 prop**

`frontend/src/components/MessageRenderer.vue` 的 `defineProps` 里追加字段与默认值：

```ts
  /** 为 true 时为 HTTP(S) 链接追加在右侧浏览器打开的操作按钮 */
  enableBrowserOpen?: boolean;
  /**
   * 只读快照模式（公开分享页）：不做依赖登录态或宿主画布的增强——
   * fs/preview 重映射、产物下载按钮、citation 角标、canvas 卡片按钮。
   */
  readonly?: boolean;
}>(), {
  theme: 'default',
  hideQuickButtons: false,
  enableBrowserOpen: false,
  readonly: false,
});
```

- [ ] **Step 4: 两处短路**

4.1 渲染前：不生成 citation 角标（`protectCitationsInMarkdown` 开头）

```ts
  /** 将 [ID:n] 转为可点击徽章（Markdown 渲染前保护，避免被解析器吞掉） */
  const protectCitationsInMarkdown = (text: string) => {
    if (!text) return '';
    // 只读快照不生成角标：点击要回调宿主打开引用面板，分享页没有那个面板。
    if (props.readonly) return text;
    return text.replace(/(?:[\[【]ID:(\w+)[\]】])|(?:Fig\.\s*(\d+))/gi, (match, id, figNum) => {
      const finalId = id || figNum;
      return `<span class="citation-badge" data-cite-id="${finalId}">${match}</span>`;
    });
  };
```

4.2 渲染后：在「智能将服务器物理绝对路径重映射」那行注释**之前**插入

```ts
    // 只读快照到此为止：后面的路径重映射（fs 预览要登录态）、产物下载按钮、
    // citation 角标兜底、路径「打开」链接，一律不能出现在公开页。
    if (props.readonly) {
      return res;
    }

    // 智能将服务器物理绝对路径重映射为可加载的网络相对路径（uploads 转静态托管，其他绝对路径转 fs 预览 API）
```

> 注意：`props.readonly` 的短路必须放在「`uploads/` 转 `/static/uploads/`」之前。`/static/` 是公开静态目录，但同一个 replace 回调里还牵着 `fs/preview` 分支，逐分支放开只会把简单问题做复杂——分享页里的本地图片本来也不该出图（原文件可能已删）。

- [ ] **Step 5: canvas 卡片降级为源码**

`canvas_html` 分支整体替换：

```html
      <!-- Canvas HTML：只读快照不提供「运行应用」，直接给出源码 -->
      <div v-else-if="segment.type === 'canvas_html'" class="my-3">
        <pre
          v-if="readonly"
          class="max-h-[420px] overflow-auto rounded-xl border border-gray-200 bg-gray-50 p-3 text-[11.5px] leading-relaxed text-gray-700 dark:border-gray-700 dark:bg-gray-800/40 dark:text-gray-300"
        ><code>{{ segment.content }}</code></pre>
        <div v-else class="p-4 rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/10 flex items-center justify-between shadow-sm">
          <div class="flex items-center space-x-3">
            <div class="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg text-blue-600 dark:text-blue-400">
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
            </div>
            <div>
              <h4 class="text-xs font-bold text-gray-800 dark:text-gray-200">{{ segment.title }}</h4>
              <p class="text-[10px] text-gray-400 dark:text-gray-500">已为您生成可交互原型页面</p>
            </div>
          </div>
          <button
            @click="emit('open-canvas', { type: 'html', title: segment.title || '', content: segment.content })"
            class="px-3 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-95 rounded-lg shadow-sm transition-all"
          >
            运行应用
          </button>
        </div>
      </div>
```

`canvas_code` 分支同样处理：外层加 `<pre v-if="readonly">` + `<div v-else>` 保留原卡片。

- [ ] **Step 6: 跑测试**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_message_renderer_readonly_contract.py -v`
Expected: 前 3 条 passed；第 4 条 `test_public_share_page_uses_readonly_renderer` 依赖 Task 12 的页面文件，Task 12 完成后补跑本文件即应 4 passed。

- [ ] **Step 7: 回归既有渲染能力**

Run: `pytest --confcutdir=tests/frontend tests/frontend -k "renderer or markdown or message" -v`
Expected: 全绿（默认 `readonly=false`，聊天页行为与改动前完全一致）

---

### Task 12: 公开分享页 `/share/:shareId`

**Files:**
- Create: `frontend/src/views/SharedConversation.vue`
- Modify: `frontend/src/router/index.ts`
- Test: `tests/frontend/test_shared_conversation_page_contract.py`

- [ ] **Step 1: 写失败测试**

```python
"""分享页契约：四态齐备、先取元信息再决定要不要密码、令牌不进 URL。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _page() -> str:
    return (ROOT / "frontend/src/views/SharedConversation.vue").read_text(encoding="utf-8")


def test_page_has_four_states():
    source = _page()
    for marker in ("phase === 'loading'", "phase === 'locked'", "phase === 'ready'", "phase === 'error'"):
        assert marker in source, marker


def test_token_is_kept_in_session_storage_not_url():
    source = _page()
    assert "sessionStorage" in source
    assert "share_token:" in source
    assert "?token=" not in source


def test_page_loads_renderer_lazily_and_readonly():
    source = _page()
    assert "defineAsyncComponent" in source
    assert "readonly" in source


def test_footer_states_readonly_snapshot():
    assert "只读快照" in _page()


def test_router_registers_public_share_route():
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
    assert "path: '/share/:shareId'" in router
    assert "public: true" in router
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_shared_conversation_page_contract.py -v`
Expected: FAIL

- [ ] **Step 3: 写页面**

```vue
<template>
  <div class="min-h-screen bg-[#f6f7fb] text-gray-900">
    <!-- 加载 -->
    <div v-if="phase === 'loading'" class="mx-auto flex min-h-screen max-w-[860px] flex-col gap-3 px-4 py-10">
      <div class="h-6 w-2/5 animate-pulse rounded-lg bg-gray-200"></div>
      <div class="h-3 w-1/4 animate-pulse rounded bg-gray-200"></div>
      <div class="mt-6 h-24 animate-pulse rounded-2xl bg-white"></div>
      <div class="h-40 animate-pulse rounded-2xl bg-white"></div>
      <p class="mt-2 text-center text-[11.5px] text-gray-400">正在加载分享内容…</p>
    </div>

    <!-- 失效 / 出错 -->
    <div v-else-if="phase === 'error'" class="grid min-h-screen place-items-center px-4">
      <div class="w-full max-w-[420px] rounded-2xl border border-gray-100 bg-white px-6 py-9 text-center shadow-sm">
        <div class="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-gray-100 text-gray-400">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.5" />
          </svg>
        </div>
        <h1 class="text-[15px] font-extrabold text-gray-900">链接不存在或已失效</h1>
        <p class="mt-1.5 text-[11.5px] leading-relaxed text-gray-400">
          {{ errorMessage || '分享可能已被作废、已过期，或链接地址不完整。' }}
        </p>
      </div>
    </div>

    <!-- 需要密码 -->
    <div v-else-if="phase === 'locked'" class="grid min-h-screen place-items-center px-4">
      <form class="w-full max-w-[400px] rounded-2xl border border-gray-100 bg-white px-6 py-7 shadow-sm" @submit.prevent="submitSecret">
        <div class="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl bg-primary/10 text-primary">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="4.5" y="10" width="15" height="10.5" rx="2.5" /><path d="M8.5 10V7a3.5 3.5 0 017 0v3" />
          </svg>
        </div>
        <h1 class="text-center text-[15px] font-extrabold text-gray-900">这是一个需要密码的分享</h1>
        <p class="mt-1.5 line-clamp-2 text-center text-[11.5px] leading-relaxed text-gray-400">
          {{ meta?.title || '对方给你分享了一段会话' }}
        </p>

        <input
          v-model="secret"
          type="password"
          autocomplete="one-time-code"
          placeholder="请输入访问密码"
          class="mt-4 h-10 w-full rounded-[9px] border border-gray-200 px-3 text-center font-mono text-[14px] tracking-[0.18em] outline-none focus:border-primary"
        />
        <p v-if="unlockError" class="mt-2 text-center text-[11px] text-red-500">{{ unlockError }}</p>

        <button
          type="submit"
          class="mt-3 h-10 w-full rounded-lg bg-primary text-[13px] font-bold text-white disabled:opacity-60"
          :disabled="unlocking || !secret.trim()"
        >
          {{ unlocking ? '验证中…' : '查看会话' }}
        </button>

        <p v-if="meta?.owner_display" class="mt-3 text-center text-[10.5px] text-gray-400">
          由 {{ meta.owner_display }} 分享<template v-if="meta?.expires_at"> · {{ expiryLabel }}</template>
        </p>
      </form>
    </div>

    <!-- 正文 -->
    <div v-else class="pb-20">
      <header class="sticky top-0 z-10 border-b border-gray-100 bg-white/85 backdrop-blur">
        <div class="mx-auto max-w-[860px] px-4 py-3.5">
          <div class="flex items-start gap-2">
            <div class="min-w-0">
              <h1 class="line-clamp-1 text-[14.5px] font-extrabold text-gray-900">{{ content?.title || '会话分享' }}</h1>
              <p class="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[10.5px] text-gray-400">
                <span v-if="content?.agent_name">来自 {{ content.agent_name }}</span>
                <span v-if="content?.agent_name">·</span>
                <span>{{ messageTurns }} 轮问答</span>
                <span>·</span>
                <span>{{ expiryLabel }}</span>
              </p>
            </div>
          </div>
        </div>
      </header>

      <main class="mx-auto max-w-[860px] px-4 py-5">
        <div class="space-y-4">
          <div v-for="(message, index) in content?.messages || []" :key="index" class="flex gap-2.5" :class="message.role === 'user' ? 'justify-end' : 'justify-start'">
            <div v-if="message.role === 'assistant'" class="mt-1 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary/10 text-[10px] font-black text-primary">
              AI
            </div>
            <div
              class="min-w-0"
              :class="message.role === 'user'
                ? 'max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-[13px] leading-relaxed text-white whitespace-pre-wrap break-words'
                : 'max-w-[calc(100%-2rem)] flex-1 rounded-2xl rounded-bl-md border border-gray-100 bg-white px-3.5 py-3 shadow-sm'"
            >
              <div v-if="message.role === 'assistant'" class="markdown-body text-[13px] leading-relaxed text-gray-800">
                <MessageRenderer
                  :content="message.content"
                  theme="default"
                  hide-quick-buttons
                  readonly
                />
              </div>
              <template v-else>{{ message.content }}</template>
            </div>
          </div>
        </div>

        <div v-if="(content?.messages || []).length === 0" class="py-16 text-center text-[12px] text-gray-400">
          这个会话没有可展示的正文
        </div>
      </main>

      <footer class="fixed inset-x-0 bottom-0 border-t border-gray-100 bg-white/90 backdrop-blur">
        <div class="mx-auto flex max-w-[860px] items-center justify-center gap-1.5 px-4 py-2.5 text-[10.5px] text-gray-400">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="4.5" y="10" width="15" height="10.5" rx="2.5" /><path d="M8.5 10V7a3.5 3.5 0 017 0v3" />
          </svg>
          只读快照 · 内容在分享创建时冻结，不会随原会话更新
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 公开只读分享页。
 *
 * 加载顺序刻意分三步：先取元信息（不带内容、不计数），再判断要不要密码，最后才取正文。
 * 令牌存 sessionStorage 而不是 URL query——URL 会进浏览器历史、Referer 和访问日志。
 */
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchShareContent,
  fetchShareMeta,
  unlockShare,
  type ShareContent,
  type ShareMeta,
} from '@/api/chatShare'
import { formatRemaining } from '@/utils/shareExpiry'

const MessageRenderer = defineAsyncComponent(() => import('@/components/MessageRenderer.vue'))

type Phase = 'loading' | 'locked' | 'ready' | 'error'

const route = useRoute()
const shareId = computed(() => String(route.params.shareId || ''))

const phase = ref<Phase>('loading')
const meta = ref<ShareMeta | null>(null)
const content = ref<ShareContent | null>(null)
const secret = ref('')
const unlocking = ref(false)
const unlockError = ref('')
const errorMessage = ref('')

const tokenKey = computed(() => `share_token:${shareId.value}`)

const messageTurns = computed(() => {
  const total = (content.value?.messages || []).filter((m) => m.role === 'user').length
  return total || meta.value?.turn_count || 0
})

const expiryLabel = computed(() => {
  if (!meta.value?.expires_at) return '永久有效'
  return formatRemaining(meta.value.expires_at)
})

function readToken(): string {
  try {
    return sessionStorage.getItem(tokenKey.value) || ''
  } catch {
    return ''
  }
}

function writeToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(tokenKey.value, token)
    else sessionStorage.removeItem(tokenKey.value)
  } catch {
    /* 隐私模式下 storage 可能不可用：退化为仅本次内存有效 */
  }
}

function markError(message: string): void {
  errorMessage.value = message
  phase.value = 'error'
}

async function loadContent(token?: string): Promise<void> {
  try {
    content.value = await fetchShareContent(shareId.value, token)
    phase.value = 'ready'
  } catch (error: any) {
    const status = error?.response?.status
    if (status === 401) {
      writeToken('')
      phase.value = 'locked'
      return
    }
    if (status === 404) {
      markError('分享可能已被作废或已过期。')
      return
    }
    markError('加载失败，请稍后重试。')
  }
}

async function boot(): Promise<void> {
  if (!shareId.value) {
    markError('链接地址不完整。')
    return
  }
  try {
    meta.value = await fetchShareMeta(shareId.value)
  } catch (error: any) {
    const status = error?.response?.status
    markError(status === 404 ? '分享可能已被作废或已过期。' : '加载失败，请稍后重试。')
    return
  }

  if (meta.value.access_mode === 'public') {
    await loadContent()
    return
  }

  const cached = readToken()
  if (cached) {
    await loadContent(cached)
    return
  }
  phase.value = 'locked'
}

async function submitSecret(): Promise<void> {
  if (!secret.value.trim() || unlocking.value) return
  unlocking.value = true
  unlockError.value = ''
  try {
    const result = await unlockShare(shareId.value, secret.value.trim())
    writeToken(result.token)
    await loadContent(result.token)
    secret.value = ''
  } catch (error: any) {
    const status = error?.response?.status
    unlockError.value =
      status === 429 ? '尝试次数过多，请稍后再试'
        : status === 404 ? '分享已失效'
          : '密码不正确，请重新输入'
  } finally {
    unlocking.value = false
  }
}

onMounted(() => {
  void boot()
})
</script>

<style scoped>
/* 分享页固定浅色：公开链接不跟随访客的系统主题，避免深色下 Markdown 对比度失控 */
:deep(.markdown-body) {
  color: #1f2937;
  word-break: break-word;
}
:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
}
:deep(.markdown-body th),
:deep(.markdown-body td) {
  border: 1px solid #e5e7eb;
  padding: 6px 10px;
  font-size: 12px;
}
</style>
```

- [ ] **Step 4: 注册 public 路由**

`frontend/src/router/index.ts`：

```ts
  {
    path: '/share/:shareId',
    name: 'shared-conversation',
    component: () => import('../views/SharedConversation.vue'),
    // 公开页面：不要求登录，守卫里直接放行
    meta: { public: true, title: '会话分享' },
  },
```

并在 `router.beforeEach` 开头加：

```ts
  if (to.meta.public) return true
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_shared_conversation_page_contract.py -v`
Expected: 5 passed

- [ ] **Step 6: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

---

### Task 13: 聊天页接线 + 全量回归

**Files:**
- Modify: `frontend/src/views/EmbedChat.vue`
- Modify: `frontend/src/components/ChatHistorySidebar.vue`（`defineExpose` 补一条）
- Test: `tests/frontend/test_chat_share_wiring_contract.py`

- [ ] **Step 1: 写失败测试**

```python
"""聊天页接线契约：漏接一处就会「点了分享没反应」。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _embed() -> str:
    return (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def test_sidebar_wires_share_button_and_headers():
    source = _embed()
    assert '@share-chat="openShareDialog"' in source
    assert ':enable-share="true"' in source
    assert ':share-auth-headers="embedAuthHeaders"' in source


def test_dialog_is_mounted_with_target_conversation():
    source = _embed()
    assert "ShareConversationDialog" in source
    assert "v-model:visible" in source or ":visible=" in source
    assert "conversation-id" in source


def test_share_target_prefers_conversation_id():
    source = _embed()
    assert "openShareDialog" in source
    assert "conversation_id" in source


def test_sidebar_exposes_reload_for_shares():
    sidebar = (ROOT / "frontend/src/components/ChatHistorySidebar.vue").read_text(encoding="utf-8")
    assert "defineExpose" in sidebar
    assert "reloadShares" in sidebar
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_chat_share_wiring_contract.py -v`
Expected: FAIL

- [ ] **Step 3: 侧栏补 `defineExpose`**

`ChatHistorySidebar.vue` 末尾（`</script>` 之前）：

```ts
/** 供宿主在创建分享后刷新「我分享的」列表。 */
defineExpose({
  reloadShares: () => sharesPanelRef.value?.reload?.(),
});
```

- [ ] **Step 4: EmbedChat 模板接线**

4.1 `<ChatHistorySidebar ...>` 上追加三个属性（放在 `@toggle-pin` 之后）：

```html
      :enable-share="true"
      :share-auth-headers="embedAuthHeaders"
      @share-chat="openShareDialog"
```

4.2 在 `<ChatHistorySidebar ... />` 之后插入弹窗：

```html
    <ShareConversationDialog
      :visible="showShareDialog"
      :conversation-id="shareTarget.conversationId"
      :conversation-title="shareTarget.title"
      :turn-count="shareTarget.turnCount"
      :running="shareTarget.running"
      :auth-headers="embedAuthHeaders"
      @close="showShareDialog = false"
      @created="handleShareCreated"
      @toast="showToast"
    />
```

- [ ] **Step 5: EmbedChat 脚本接线**

在 `import ChatHistorySidebar from "@/components/ChatHistorySidebar.vue";` 之后加：

```ts
import ShareConversationDialog from "@/components/ShareConversationDialog.vue";
```

在 `const showToast = toast.showToast;` 之后加：

```ts
/** 会话在线分享：侧栏只 emit，取数与提交都在弹窗里完成。 */
const showShareDialog = ref(false);
const shareTarget = ref({
  conversationId: "",
  title: "",
  turnCount: 0,
  running: false,
});

const openShareDialog = (item: any) => {
  const targetConvId = String(item?.conversation_id || "");
  if (!targetConvId) {
    // 按单轮 trace 聚合的条目没有 conversation_id：没有稳定会话可冻结。
    showToast("该记录没有会话 ID，暂不支持分享", "warning");
    return;
  }
  shareTarget.value = {
    conversationId: targetConvId,
    title: String(item?.title || item?.query || "未命名会话").slice(0, 120),
    turnCount: Number(item?.turn_count || item?.count || 0),
    running: Boolean(item?.running),
  };
  showShareDialog.value = true;
};

const handleShareCreated = (created: any) => {
  showToast("分享链接已创建", "success");
  historySidebarRef.value?.reloadShares?.();
};
```

4.3 侧栏加 ref（`<ChatHistorySidebar` 上）：

```html
      ref="historySidebarRef"
```

脚本里补声明（放在 `const showShareDialog = ref(false);` 之前）：

```ts
const historySidebarRef = ref<InstanceType<typeof ChatHistorySidebar> | null>(null);
```

> 注意：`ChatHistorySidebar` 是用 `import` 引入的组件，`InstanceType<typeof ChatHistorySidebar>` 对 `<script setup>` 组件有效。

4.4 核对侧栏条目里 `turn_count` 字段是否存在：

Run: `grep -n "turn_count" frontend/src/components/ChatHistorySidebar.vue frontend/src/views/EmbedChat.vue | head`
若条目里没有该字段，改为按 `historyList` 现算：

```ts
    turnCount: Number(
      item?.turn_count
      || (Array.isArray(historyList.value)
        ? historyList.value.filter((row: any) => row?.conversation_id === targetConvId).length
        : 0),
    ),
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest --confcutdir=tests/frontend tests/frontend/test_chat_share_wiring_contract.py -v`
Expected: 4 passed

- [ ] **Step 7: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: 无错误

- [ ] **Step 8: 后端全量回归**

Run: `pytest tests/services/test_conversation_share_service.py tests/services/test_conversation_share_repository.py tests/api/portal/test_conversation_shares_api.py tests/api/test_public_share_api.py tests/test_conversation_share_migration_contract.py -v`
Expected: 全绿

- [ ] **Step 9: 前端契约全量回归**

Run: `pytest --confcutdir=tests/frontend tests/frontend -v`
Expected: 全绿

- [ ] **Step 10: 手动验收（用户或本人在浏览器点一遍）**

1. 聊天页历史侧栏 → 条目 hover → 分享图标 → 弹窗；
2. 选「密钥访问」+「7 天」→ 创建 → 结果态复制链接与密码；
3. 新标签打开链接 → 输密码 → 看到正文（表格、图表、Mermaid 正常）；
4. 侧栏「我分享的」→ 打开次数 1、状态「生效中」；
5. 点「作废」→ 刷新分享页 → 「链接不存在或已失效」；
6. 再创建一条「公开 + 永久」→ 无密码直接可见，永不过期。

- [ ] **Step 11: 更新测试清单**

`tests/CHECKLIST.md` 追加一行记录本特性的测试入口（后端 4 个文件 + 前端 6 个契约文件）。

---

## 收尾：改动清单（供用户提交时参考）

**新增**

```
app/models/conversation_share.py
app/services/conversation_share_service.py
app/api/portal/endpoints/chat_shares.py
app/api/v1/endpoints/share.py
db-prod/V197-create-conversation-shares.sql
db-prod-pg/V97-create-conversation-shares.sql
frontend/src/utils/shareExpiry.ts
frontend/src/api/chatShare.ts
frontend/src/components/ShareConversationDialog.vue
frontend/src/components/SharedLinksPanel.vue
frontend/src/views/SharedConversation.vue
tests/services/test_conversation_share_model.py
tests/services/test_conversation_share_service.py
tests/services/test_conversation_share_repository.py
tests/api/portal/test_conversation_shares_api.py
tests/api/test_public_share_api.py
tests/test_conversation_share_migration_contract.py
tests/frontend/share_expiry.mjs
tests/frontend/test_share_expiry_contract.py
tests/frontend/test_chat_share_api_contract.py
tests/frontend/test_share_dialog_contract.py
tests/frontend/test_shared_links_panel_contract.py
tests/frontend/test_message_renderer_readonly_contract.py
tests/frontend/test_shared_conversation_page_contract.py
tests/frontend/test_chat_share_wiring_contract.py
```

**修改**

```
app/api/portal/api.py
app/api/v1/api.py
frontend/src/components/ChatHistorySidebar.vue
frontend/src/components/MessageRenderer.vue
frontend/src/router/index.ts
frontend/src/views/EmbedChat.vue
tests/CHECKLIST.md
```

**建议 commit 切分（用户自行执行）**

1. `feat(db): 新增会话在线分享表（MySQL V197 / PG V97）`
2. `feat(api): 会话分享快照、密钥访问与访问计数`
3. `feat(web): 会话分享弹窗、我分享的列表与公开分享页`
4. `test: 会话分享服务、接口与前端契约`

## 风险与回滚

| 风险 | 影响 | 处置 |
|---|---|---|
| 迁移未执行就上线接口 | 接口 500（表不存在） | 部署顺序：先迁移再发布；接口层捕获 `ProgrammingError` 返回 503 并记录明确日志 |
| `ENCRYPTION_KEY` 轮换 | 老分享密码无法解密 | 列表里 `secret=null`，前端禁用「复制密码」并提示「请重新创建分享」 |
| Redis 不可用 | 解锁限流失效（放行） | 已按 fail-open 实现：`redis is None` 直接返回，不阻断正版用户 |
| 分享页被搜索引擎收录 | 私密内容外泄 | 服务端 `X-Robots-Tag: noindex, nofollow`；前端路由 `meta.public` |
| 会话正文含敏感数据 | 分享人主动外发 | 设计上由分享人自行判断；正文只保留问答，不含工具参数与思考 |
| 快照过大拖慢响应 | 单次响应 > 2 MB | 创建时硬拦（>500 轮或 >2 MB 返回 400） |

**回滚**：下掉两个路由注册即可让接口 404，已发出的链接立即失效；表保留不删（`content_json` 是唯一数据源，删除前先确认没有用户在用）。
