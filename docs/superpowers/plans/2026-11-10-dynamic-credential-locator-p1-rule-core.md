# 凭据定位规则（P1 规则内核）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目约定（覆盖通用流程）**：**不要执行 `git commit`**。每个任务末尾的「检查点」是把改动与验证结果汇报给用户，由用户决定何时提交。也不要运行 `./dev.sh`、部署脚本或生产库操作。

**Goal:** 让一个槽位的凭据从「绑死一条」升级为「绑一条定位规则」——按这次调用的入参值精确匹配「调用者可见」的凭据；匹配不上时按规则自带的策略处理（失败 / 回退固定 / 交给模型选择）。

**Architecture:** 新增纯函数模块 `app/services/secrets/locator.py`（规则解析 + 候选值生成 + 匹配），由 `resolver._locate()` 在既有的 `_pick_binding()` 位置替换取绑定逻辑。**定位只决定候选，四道闸门（可见范围 → grant → 策略 → 解密与流水）一行不改**：`_locate` 返回 secret_id 后，`resolve()` 的后续代码原样执行。注入层只增加三个透传参数（`user_attrs` / `rule_args` / `preferred_secret_id`），不改注入逻辑本身。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / Pydantic 2 / MySQL + PostgreSQL（双份迁移）/ pytest。

**设计文档：** [`docs/superpowers/specs/2026-11-10-dynamic-credential-locator-design.md`](../specs/2026-11-10-dynamic-credential-locator-design.md)

**P1 的可独立交付性：** 做完 P1，数据库与接口已经支持规则绑定与试算；现有界面不受影响（`strategy` 默认 `fixed`，所有存量行行为不变）。P2（配置界面）、P3（模型推断 + `list_my_credentials`）在 P1 收尾后单独出计划。

---

## 实现者必读的五个现状事实

1. **一个槽位最多两条绑定**（本次迁移后）：`(consumer_type, consumer_id, slot, bound_by_user_id, strategy)` 唯一。个人层与全局层各自可以「规则 + 固定」各一条。
2. **`sys_secret_bindings.bound_by_user_id` 用空串表示全局**，不是 NULL（V209/V109 改过，模型已对齐 `default=""`）。查询时仍要兼容 `IS NULL OR = ''` 的历史写法。
3. **`list_visible_secrets(session, user_id=..., dept_code=...)`**（`app/services/secrets/center.py:606`）就是「可见集合」的唯一定义：本人的 + 被授权的系统级。**规则的精确匹配必须在这个结果集里做**，不能直接查 `sys_secrets` 全表——那是越权。
4. **`resolve()` 的现有顺序**：`_pick_binding` → `session.get(UserSecret, binding.secret_id)` → 系统级则 `_has_grant` → `_platform_flags` → `evaluate_secret` → 解密 → `_log_usage`。`_locate` 只替换第一步。
5. **注入层只关心 `result.ok` 与 `result.value`**：`apply_mcp_bindings` / `apply_mcp_arg_bindings` / `apply_api_tool_param_bindings` 都是「列槽位 → 逐个 `resolve(slot=...)`」。改 `resolve` 的签名必须给新参数默认值，否则会打断全部调用点。

---

## File Structure

**新增**

| 文件 | 职责 |
|---|---|
| `app/services/secrets/locator.py` | 规则的一切纯逻辑：`RuleSpec`、`parse_rule()`、`build_candidates()`、`pick_match()`、`rule_summary()`。不 import 数据库、不解密。试算与运行期共用。 |
| `db-prod/V211-add-credential-locator.sql` | MySQL：`secret_id` 可空、加 `strategy` / `rule_json`、加新唯一索引 |
| `db-prod/V212-drop-legacy-binding-index.sql` | MySQL：删旧唯一索引 `uk_binding_slot` |
| `db-prod-pg/V111-add-credential-locator.sql` | PG 同上（`IF NOT EXISTS` 写法） |
| `db-prod-pg/V112-drop-legacy-binding-index.sql` | PG：删旧索引 |
| `tests/secrets/test_locator_rule.py` | 纯函数测试（无 DB、无网络） |
| `tests/secrets/test_locator_resolve.py` | `_locate` 顺序与 `on_miss` 语义（fake session） |

**修改**

| 文件 | 改动 |
|---|---|
| `app/models/secret.py` | `SecretBinding`：`secret_id` 改 `nullable=True`；新增 `strategy` / `rule_json` |
| `app/services/secrets/resolver.py` | 新增 `_bindings_for_slot()` / `_locate()` / `_visible_index()`；`resolve()` 加三个透传参数并接入 `_locate`；新增两个 denial 常量 |
| `app/services/secrets/center.py` | 新增 `bind_rule()`；`delete_binding` 支持按 strategy 删（需保留同槽位的另一条） |
| `app/services/secrets/injection.py` | 三个注入函数透传 `user_attrs` / `rule_args`；`_current_user` 旁新增 `_current_user_attrs` |
| `app/api/portal/endpoints/secrets.py` | `BindRequest` 加 `strategy` / `rule`；`POST /bindings` 分流到 `bind_rule`；`GET /bindings` 改 LEFT JOIN 并返回 `strategy` / `rule_summary`；新增 `POST /locate-preview` |
| `tests/CHECKLIST.md` | 本轮交付行 |

**为什么 `locator.py` 单独成文件：** 规则逻辑要被三个地方用（运行期解析、试算端点、将来的 P3 候选注入），且它是唯一能纯函数测试的部分。放进 `resolver.py`（已有 400 行）会让它同时承担「查库」和「算字符串」两件事。

---

## Task 1: 双库迁移

**Files:**
- Create: `db-prod/V211-add-credential-locator.sql`
- Create: `db-prod/V212-drop-legacy-binding-index.sql`
- Create: `db-prod-pg/V111-add-credential-locator.sql`
- Create: `db-prod-pg/V112-drop-legacy-binding-index.sql`

**为什么拆成两个文件：** 先建新索引、再删旧索引。反过来（先删后建）会出现「旧索引已删、新索引未建」的裸窗口，并发写入可能插进重复槽位行。执行器按文件名顺序跑，两个文件天然分两次。

- [ ] **Step 1: 写 MySQL 迁移（V211）**

```sql
-- V211：凭据绑定从「一条 secret_id」升级为「一条选择器」
--   strategy = fixed → 就是今天的行为，secret_id 必填
--   strategy = rule  → 按 rule_json 在「调用者可见」的凭据里定位，secret_id 为空

ALTER TABLE `sys_secret_bindings`
  MODIFY COLUMN `secret_id` VARCHAR(36) NULL
    COMMENT 'strategy=fixed 时必填；strategy=rule 时为空';

ALTER TABLE `sys_secret_bindings`
  ADD COLUMN `strategy` VARCHAR(16) NOT NULL DEFAULT 'fixed'
    COMMENT 'fixed / rule：绑一条凭据，还是绑一条定位规则',
  ADD COLUMN `rule_json` TEXT NULL
    COMMENT 'strategy=rule 时的匹配规则（JSON）';

-- 唯一索引加入 strategy：同一槽位允许「规则 + 固定（回退目标）」各一条。
-- 旧索引 uk_binding_slot 在 V212 里才删——先建后删，不留裸窗口。
ALTER TABLE `sys_secret_bindings`
  ADD UNIQUE KEY `uk_binding_slot_strategy`
    (`consumer_type`, `consumer_id`, `slot`, `bound_by_user_id`, `strategy`);
```

- [ ] **Step 2: 写 MySQL 删旧索引（V212）**

```sql
-- V212：新索引 uk_binding_slot_strategy 已覆盖旧索引的约束（前缀相同且更严）
ALTER TABLE `sys_secret_bindings` DROP INDEX `uk_binding_slot`;
```

- [ ] **Step 3: 写 PG 迁移（V111）**

PG 执行器**不吞错误**（与 MySQL 侧不同），所以每一步都要 `IF NOT EXISTS` 自查幂等。

```sql
ALTER TABLE "sys_secret_bindings" ALTER COLUMN "secret_id" DROP NOT NULL;

ALTER TABLE "sys_secret_bindings"
  ADD COLUMN IF NOT EXISTS "strategy" VARCHAR(16) NOT NULL DEFAULT 'fixed',
  ADD COLUMN IF NOT EXISTS "rule_json" TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS "uk_binding_slot_strategy"
  ON "sys_secret_bindings" ("consumer_type", "consumer_id", "slot", "bound_by_user_id", "strategy");
```

- [ ] **Step 4: 写 PG 删旧索引（V112）**

```sql
DROP INDEX IF EXISTS "uk_binding_slot";
```

- [ ] **Step 5: 校验两套 SQL 的编号不冲突、不越号**

```bash
ls db-prod | grep -E "^V21[0-9]-" | sort -V | tail -3
ls db-prod-pg | grep -E "^V11[0-9]-" | sort -V | tail -3
```

Expected: MySQL 末尾是 `V210-…` 然后本计划的 `V211` / `V212`；PG 末尾是 `V110-…` 然后 `V111` / `V112`。若 `V211`/`V111` 已被别的分支占用，**停下来汇报**，顺延编号，不要覆盖已有文件。

- [ ] **Step 6: 检查点**

不提交、不执行。汇报：四个文件、SQL 摘要、编号占用情况。

---

## Task 2: 模型字段

**Files:**
- Modify: `app/models/secret.py`（`SecretBinding`）
- Test: `tests/secrets/test_locator_rule.py`（先建文件，本任务只放模型断言）

- [ ] **Step 1: 写失败测试**

```python
"""规则定位：纯函数 + 模型字段的最小断言（无 DB、无网络）。"""
from app.models.secret import SecretBinding


def test_binding_has_strategy_and_rule_json_columns():
    cols = SecretBinding.__table__.columns
    assert "strategy" in cols
    assert "rule_json" in cols
    # 默认值是 fixed：存量行与所有未显式传 strategy 的写入都应按固定绑定处理
    assert cols["strategy"].default.arg == "fixed"


def test_binding_secret_id_is_nullable():
    assert SecretBinding.__table__.columns["secret_id"].nullable is True
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_rule.py -v
```

Expected: FAIL —— `"strategy" in cols` 为 False（列还不存在）。

- [ ] **Step 3: 改模型**

```python
class SecretBinding(Base):
    """凭据能被谁取到：白名单，默认零授权。"""

    __tablename__ = "sys_secret_bindings"

    id = Column(String(36), primary_key=True)
    # 刻意不声明外键：与 ``sys_secret_usage_logs`` 同理（可单独导出/导入）。
    #: ``strategy='fixed'`` 时必填；``strategy='rule'`` 时为空——规则落在 rule_json。
    secret_id = Column(String(36), nullable=True)
    consumer_type = Column(String(24), nullable=False)
    consumer_id = Column(String(36), nullable=False)
    slot = Column(String(128), nullable=False)
    bound_by_user_id = Column(String(64), nullable=False, default="")
    #: ``fixed``（绑一条凭据）/ ``rule``（绑一条定位规则）
    strategy = Column(String(16), nullable=False, default="fixed", server_default="fixed")
    #: ``strategy='rule'`` 时的规则 JSON；见 ``app/services/secrets/locator.py``
    rule_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

`Text` 需已在文件顶部导入；若没有，在 `from sqlalchemy import ...` 那一行补 `Text`。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_rule.py -v
```

Expected: 2 passed。

- [ ] **Step 5: 检查点**

汇报模型改动；提醒用户迁移文件尚未执行（P1 落地时需要用户在控制台跑，或按其既有流程）。

---

## Task 3: 规则纯函数 `locator.py`

**Files:**
- Create: `app/services/secrets/locator.py`
- Test: `tests/secrets/test_locator_rule.py`

- [ ] **Step 1: 写失败测试（追加到同一测试文件）**

```python
import pytest

from app.services.secrets.locator import (
    RuleError,
    build_candidates,
    parse_rule,
    pick_match,
    rule_summary,
)


class _Secret:
    """只带匹配需要的那三个字段，避免测试依赖完整模型。"""

    def __init__(self, secret_key, name, category, id="s"):
        self.secret_key, self.name, self.category, self.id = secret_key, name, category, id


def test_parse_rule_accepts_arg_key_and_normalizes():
    spec = parse_rule({
        "key_source": "arg:to",
        "transforms": ["trim", "lower"],
        "templates": ["{value}", "{value}-smtp"],
        "target": "secret_key",
        "match": "exact",
        "on_miss": "model",
    })
    assert spec.key_source == "arg:to"
    assert spec.arg_name == "to"
    assert spec.transforms == ("trim", "lower")
    assert spec.templates == ("{value}", "{value}-smtp")
    assert spec.on_miss == "model"


def test_parse_rule_rejects_unknown_field_and_bad_enum():
    with pytest.raises(RuleError, match="未知字段"):
        parse_rule({"key_source": "arg:to", "secret_id": "abc"})
    with pytest.raises(RuleError, match="on_miss"):
        parse_rule({"key_source": "arg:to", "on_miss": "retry"})


def test_parse_rule_rejects_too_many_templates_and_missing_placeholder():
    with pytest.raises(RuleError, match="最多 3 条"):
        parse_rule({"key_source": "arg:to", "templates": ["a", "b", "c", "d"]})
    with pytest.raises(RuleError, match="\\{value\\}"):
        parse_rule({"key_source": "arg:to", "templates": ["fixed-name"]})


def test_build_candidates_applies_transforms_in_order():
    spec = parse_rule({
        "key_source": "arg:to",
        "transforms": ["trim", "lower"],
        "templates": ["mail:{value}"],
    })
    assert build_candidates(spec, identity={}, args={"to": "  ZhangXM@nanzi.com  "}) == ["mail:zhangxm@nanzi.com"]


def test_build_candidates_strip_domain_is_noop_without_at():
    spec = parse_rule({"key_source": "arg:to", "transforms": ["strip_domain"], "templates": ["{value}"]})
    assert build_candidates(spec, identity={}, args={"to": "A"}) == ["A"]


def test_build_candidates_returns_empty_when_arg_missing():
    spec = parse_rule({"key_source": "arg:to", "templates": ["{value}"]})
    assert build_candidates(spec, identity={}, args={"subject": "hi"}) == []


def test_build_candidates_from_identity():
    spec = parse_rule({"key_source": "user.email", "transforms": ["strip_domain"], "templates": ["mail:{value}"]})
    assert build_candidates(spec, identity={"email": "a@b.com"}, args={}) == ["mail:a"]


def test_pick_match_tries_templates_in_order():
    spec = parse_rule({
        "key_source": "arg:to",
        "templates": ["{value}-smtp", "{value}"],
        "on_miss": "fail",
    })
    secrets = [_Secret("A", "A 的邮箱", "mail"), _Secret("A-smtp", "A 的 SMTP", "mail")]
    hit, wanted = pick_match(spec, ["A", "A-smtp"], secrets)
    # 第一个模板 {value}-smtp 生成的 "A-smtp" 命中，说明模板顺序生效
    assert (hit.secret_key, wanted) == ("A-smtp", "A-smtp")


def test_pick_match_exact_vs_exact_ci():
    secrets = [_Secret("CC", "C 的邮箱", "mail")]
    strict = parse_rule({"key_source": "arg:to", "match": "exact", "templates": ["{value}"]})
    loose = parse_rule({"key_source": "arg:to", "match": "exact_ci", "templates": ["{value}"]})
    assert pick_match(strict, ["cc"], secrets)[0] is None
    assert pick_match(loose, ["cc"], secrets)[0].secret_key == "CC"


def test_pick_match_can_target_name_and_category():
    secrets = [_Secret("k1", "A 的邮箱账号", "mail-account")]
    by_name = parse_rule({"key_source": "arg:to", "target": "name", "templates": ["{value}"]})
    by_cat = parse_rule({"key_source": "arg:to", "target": "category", "templates": ["{value}"]})
    assert pick_match(by_name, ["A 的邮箱账号"], secrets)[0].secret_key == "k1"
    assert pick_match(by_cat, ["mail-account"], secrets)[0].secret_key == "k1"


def test_rule_summary_is_human_readable():
    spec = parse_rule({"key_source": "arg:to", "templates": ["{value}"], "on_miss": "model"})
    assert rule_summary(spec) == "secret_key = {入参 to}"


def test_parse_rule_rejects_rule_carrying_secret_id():
    """规则只描述「怎么找」，绝不描述「找到谁」。"""
    with pytest.raises(RuleError, match="未知字段"):
        parse_rule({"key_source": "arg:to", "templates": ["{value}"], "secret_id": "x"})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_rule.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.secrets.locator'`。

- [ ] **Step 3: 实现 `locator.py`**

```python
"""凭据定位规则：把「这次调用的入参」翻译成「要找的 secret_key」。

**纯函数，不查库、不解密。** 运行期解析（``resolver._locate``）与配置页试算
（``POST /secrets/locate-preview``）走的是同一段逻辑，否则「试算说能命中、
真跑却失败」这类问题永远查不清。

规则描述的是**怎么找**，不是**找到谁**：``parse_rule`` 会拒绝任何 ``secret_id``，
所以改一条规则永远变不成「读别人凭据」的手段。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: 身份类键来源（补充能力，主场景是 ``arg:<入参名>``）
USER_KEY_SOURCES = (
    "user.email",
    "user.user_name",
    "user.real_name",
    "user.dept_code",
    "user.user_id",
)
TRANSFORMS = ("trim", "strip_domain", "lower")
TARGETS = ("secret_key", "name", "category")
MATCHES = ("exact", "exact_ci")
ON_MISS = ("fail", "fallback_fixed", "model")
MAX_TEMPLATES = 3
ALLOWED_FIELDS = {"key_source", "transforms", "templates", "target", "match", "on_miss"}


class RuleError(ValueError):
    """规则不合法。``str(exc)`` 直接展示给配置者，所以要写人话。"""


@dataclass(frozen=True)
class RuleSpec:
    key_source: str
    transforms: tuple[str, ...] = ()
    templates: tuple[str, ...] = ("{value}",)
    target: str = "secret_key"
    match: str = "exact"
    on_miss: str = "fail"

    @property
    def is_arg_key(self) -> bool:
        return self.key_source.startswith("arg:")

    @property
    def arg_name(self) -> str | None:
        if not self.is_arg_key:
            return None
        return self.key_source[4:] or None


def _as_mapping(raw: str | bytes | Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw == "" or raw == b"":
        raise RuleError("规则为空")
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuleError(f"规则不是合法 JSON：{exc}") from exc
    else:
        data = dict(raw)
    if not isinstance(data, dict):
        raise RuleError("规则必须是一个 JSON 对象")
    return data


def parse_rule(raw: str | bytes | Mapping[str, Any] | None) -> RuleSpec:
    """校验并归一化规则。任何不合法的写法都在保存时就被拒绝。"""
    data = _as_mapping(raw)

    unknown = set(data) - ALLOWED_FIELDS
    if unknown:
        # secret_id 落在这里：规则不允许指定具体凭据
        raise RuleError(f"未知字段：{', '.join(sorted(unknown))}")

    key_source = str(data.get("key_source") or "").strip()
    if not key_source:
        raise RuleError("缺少 key_source（用哪个值去匹配）")
    if key_source.startswith("arg:"):
        if not key_source[4:].strip():
            raise RuleError("key_source 写成 arg: 时必须给出参数名，例如 arg:to")
    elif key_source not in USER_KEY_SOURCES:
        raise RuleError(
            f"key_source 不支持 {key_source!r}；可用 arg:<参数名> 或 "
            + " / ".join(USER_KEY_SOURCES)
        )

    transforms = data.get("transforms") or []
    if not isinstance(transforms, (list, tuple)):
        raise RuleError("transforms 必须是数组")
    bad = [t for t in transforms if t not in TRANSFORMS]
    if bad:
        raise RuleError(f"未知变换 {bad[0]!r}；可用 {' / '.join(TRANSFORMS)}")
    if len(set(transforms)) != len(transforms):
        raise RuleError("transforms 有重复项")

    templates = data.get("templates") or ["{value}"]
    if not isinstance(templates, (list, tuple)) or not templates:
        raise RuleError("templates 必须是非空数组")
    if len(templates) > MAX_TEMPLATES:
        raise RuleError(f"templates 最多 3 条，当前 {len(templates)} 条")
    for tpl in templates:
        if not isinstance(tpl, str) or "{value}" not in tpl:
            raise RuleError("每条 template 都必须包含 {value} 占位符")

    target = data.get("target") or "secret_key"
    if target not in TARGETS:
        raise RuleError(f"target 不支持 {target!r}；可用 {' / '.join(TARGETS)}")

    match = data.get("match") or "exact"
    if match not in MATCHES:
        raise RuleError(f"match 不支持 {match!r}；可用 {' / '.join(MATCHES)}")

    on_miss = data.get("on_miss") or "fail"
    if on_miss not in ON_MISS:
        raise RuleError(f"on_miss 不支持 {on_miss!r}；可用 {' / '.join(ON_MISS)}")

    return RuleSpec(
        key_source=key_source,
        transforms=tuple(transforms),
        templates=tuple(templates),
        target=target,
        match=match,
        on_miss=on_miss,
    )


def _apply_transforms(value: str, transforms: Sequence[str]) -> str:
    for name in transforms:
        if name == "trim":
            value = value.strip()
        elif name == "lower":
            value = value.lower()
        elif name == "strip_domain":
            # 不含 @ 时是空操作：同一套规则可以挂在邮箱键与其他键上
            value = value.split("@", 1)[0]
    return value


def build_candidates(
    spec: RuleSpec,
    *,
    identity: Mapping[str, Any] | None = None,
    args: Mapping[str, Any] | None = None,
) -> list[str]:
    """算出「按顺序要去找的那几个名字」。取不到值返回空列表（→ 未命中）。"""
    identity = identity or {}
    args = args or {}
    if spec.is_arg_key:
        raw = args.get(spec.arg_name) if spec.arg_name else None
    else:
        raw = identity.get(spec.key_source.partition(".")[2])
    if raw is None or raw == "":
        return []
    value = _apply_transforms(str(raw), spec.transforms)
    return [tpl.replace("{value}", value) for tpl in spec.templates]


def _field_of(secret: Any, target: str) -> str | None:
    raw = getattr(secret, target, None)
    return None if raw is None else str(raw)


def pick_match(
    spec: RuleSpec,
    wanted: Sequence[str],
    secrets: Sequence[Any],
) -> tuple[Any | None, str]:
    """在给定凭据里找第一条命中的。返回 ``(凭据 or None, 实际用的待匹配值)``。

    ``wanted`` 的顺序就是模板顺序——第一条命中的即生效。
    """
    for candidate in wanted:
        for secret in secrets:
            field = _field_of(secret, spec.target)
            if field is None:
                continue
            if spec.match == "exact_ci":
                if field.lower() == candidate.lower():
                    return secret, candidate
            elif field == candidate:
                return secret, candidate
    return None, wanted[0] if wanted else ""


_TARGET_LABEL = {"secret_key": "secret_key", "name": "名称", "category": "分类"}


def rule_summary(spec: RuleSpec) -> str:
    """给界面用的一句话摘要，例如 ``secret_key = {入参 to}``。"""
    if spec.is_arg_key:
        source = f"入参 {spec.arg_name}"
    else:
        source = {
            "user.email": "我的邮箱",
            "user.user_name": "我的账号名",
            "user.real_name": "我的姓名",
            "user.dept_code": "我的部门",
            "user.user_id": "我的用户 ID",
        }.get(spec.key_source, spec.key_source)
    return f"{_TARGET_LABEL.get(spec.target, spec.target)} = {spec.templates[0]}（{source}）".replace(
        "（", "：取"
    ).replace("'", "")
```

`rule_summary` 的字符串拼接太绕，直接用下面这版，避免下次读代码的人去猜：

```python
def rule_summary(spec: RuleSpec) -> str:
    """给界面用的一句话摘要，例如 ``secret_key = {入参 to}``。"""
    if spec.is_arg_key:
        source = f"入参 {spec.arg_name}"
    else:
        source = {
            "user.email": "我的邮箱",
            "user.user_name": "我的账号名",
            "user.real_name": "我的姓名",
            "user.dept_code": "我的部门",
            "user.user_id": "我的用户 ID",
        }.get(spec.key_source, spec.key_source)
    target = _TARGET_LABEL.get(spec.target, spec.target)
    return f"{target} = {spec.templates[0]}（取{source}）"
```

同时把上面 `test_rule_summary_is_human_readable` 的期望值改成 `"secret_key = {value}（取入参 to）"`。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_rule.py -v
```

Expected: 全部 passed（模型 2 条 + 规则 11 条）。

- [ ] **Step 5: 检查点**

汇报：`locator.py` 行数、测试通过数；说明 `parse_rule` 拒绝 `secret_id` 这一条是「规则无法越权」的第一道保障。

---

## Task 4: `resolver._locate()` —— 层内规则优先、`on_miss` 不越层

**Files:**
- Modify: `app/services/secrets/resolver.py`
- Test: `tests/secrets/test_locator_resolve.py`

- [ ] **Step 1: 写失败测试（fake session，不碰真库）**

```python
"""``_locate`` 的顺序与 on_miss 语义。用 fake session，跑得比真库快且无副作用。"""
import json

import pytest

from app.services.secrets.resolver import SecretResolver


class _Row:
    def __init__(self, id, secret_key, name="n", category="c", strategy="fixed", rule=None, bound_by=""):
        self.id = id
        self.secret_key = secret_key
        self.name = name
        self.category = category
        self.strategy = strategy
        self.rule_json = json.dumps(rule) if rule else None
        self.bound_by_user_id = bound_by


def rule(to_miss="fail", templates=None):
    return {
        "key_source": "arg:to",
        "templates": templates or ["{value}"],
        "target": "secret_key",
        "on_miss": to_miss,
    }


@pytest.mark.asyncio
async def test_personal_fixed_beats_global_rule():
    r = SecretResolver()
    rows = [
        _Row("g-rule", "", strategy="rule", rule=rule(), bound_by=""),
        _Row("mine", "A", bound_by="u1"),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="arg:send_mail:api_key",
        user_id="u1", dept_code=None, identity={}, args={"to": "A"}, rows=rows,
        visible=[_Row("mine", "A")],
    )
    assert located.secret_id == "mine" and located.strategy == "fixed"


@pytest.mark.asyncio
async def test_global_rule_hits_before_global_fixed():
    r = SecretResolver()
    rows = [
        _Row("g-fixed", "fallback", bound_by=""),
        _Row("g-rule", "", strategy="rule", rule=rule(), bound_by=""),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "A"}, rows=rows,
        visible=[_Row("hit", "A")],
    )
    assert located.strategy == "rule" and located.secret_id == "hit"


@pytest.mark.asyncio
async def test_rule_miss_with_fail_does_not_fall_through_to_global_fixed():
    r = SecretResolver()
    rows = [
        _Row("g-rule", "", strategy="rule", rule=rule("fail"), bound_by=""),
        _Row("g-fixed", "fallback", bound_by=""),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[_Row("other", "A")],
    )
    assert located.secret_id is None
    assert located.denial == "rule_miss"


@pytest.mark.asyncio
async def test_rule_miss_with_fallback_uses_same_layer_fixed():
    r = SecretResolver()
    rows = [
        _Row("g-rule", "", strategy="rule", rule=rule("fallback_fixed"), bound_by=""),
        _Row("g-fixed", "fallback", bound_by=""),
    ]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[],
    )
    assert located.secret_id == "g-fixed" and located.fallback_used is True


@pytest.mark.asyncio
async def test_rule_miss_with_model_choice_uses_preferred_when_visible():
    r = SecretResolver()
    rows = [_Row("g-rule", "", strategy="rule", rule=rule("model"), bound_by="")]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[_Row("c3", "CC")], preferred_secret_id="c3",
    )
    assert located.secret_id == "c3" and located.strategy == "model_choice"


@pytest.mark.asyncio
async def test_model_choice_outside_visible_is_denied():
    r = SecretResolver()
    rows = [_Row("g-rule", "", strategy="rule", rule=rule("model"), bound_by="")]
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "C"}, rows=rows,
        visible=[_Row("c1", "A")], preferred_secret_id="someone-else",
    )
    assert located.secret_id is None and located.denial == "rule_miss"


@pytest.mark.asyncio
async def test_broken_rule_json_is_treated_as_miss_not_crash():
    r = SecretResolver()
    bad = _Row("g-rule", "", strategy="rule", bound_by="")
    bad.rule_json = "{not json"
    located = await r._locate(
        None, consumer_type="mcp_server", consumer_id="c", slot="s",
        user_id="u1", dept_code=None, identity={}, args={"to": "A"}, rows=[bad],
        visible=[],
    )
    assert located.secret_id is None and located.denial == "rule_miss"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py -v
```

Expected: FAIL —— `AttributeError: 'SecretResolver' object has no attribute '_locate'`。

- [ ] **Step 3: 在 `resolver.py` 加 `Located` 与 `_locate`**

放在 `_pick_binding` 之前，让「取绑定」的两种实现挨着。注意 `rows` / `visible` / `preferred_secret_id` 都是可选注入参数：生产路径由 `_locate` 内部查询填充，测试直接传，避免为测试造数据库。

```python
@dataclass
class Located:
    """定位结果。``secret_id`` 为空表示没找到可用凭据，``denial`` 说明原因。"""

    secret_id: str | None = None
    strategy: str = "fixed"
    fallback_used: bool = False
    denial: str | None = None
    detail: str = ""


async def _locate(
    self,
    session,
    *,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    user_id: str,
    dept_code: str | None,
    identity: Mapping[str, Any] | None = None,
    args: Mapping[str, Any] | None = None,
    preferred_secret_id: str | None = None,
    rows: Sequence[Any] | None = None,
    visible: Sequence[Any] | None = None,
) -> Located:
    """选出这次要用的凭据。**层间**个人优先，**层内**规则优先。

    ``on_miss=fail`` 时绝不继续往下取固定绑定（同层、跨层都不取）——否则
    「回退」就成了默认行为，配错的规则永远没人发现。
    """
    from app.services.secrets.locator import RuleError, build_candidates, parse_rule, pick_match

    if rows is None:
        rows = await self._bindings_for_slot(session, consumer_type, consumer_id, slot, user_id)
    if not rows:
        return Located(denial=None)  # 交给调用方按「无绑定」处理

    layers: list[list[Any]] = []
    mine = [b for b in rows if (b.bound_by_user_id or "") == user_id]
    glob = [b for b in rows if (b.bound_by_user_id or "") == ""]
    if mine:
        layers.append(mine)
    if glob:
        layers.append(glob)

    for layer in layers:
        rule_binding = next((b for b in layer if (b.strategy or "fixed") == "rule"), None)
        fixed_binding = next((b for b in layer if (b.strategy or "fixed") != "rule"), None)

        if rule_binding is None:
            if fixed_binding is not None and fixed_binding.secret_id:
                return Located(secret_id=fixed_binding.secret_id, strategy="fixed")
            continue

        try:
            spec = parse_rule(rule_binding.rule_json)
        except RuleError as exc:
            # 坏规则按未命中处理：不崩、不静默换人，交由 on_miss 语义
            logger.warning("[SecretLocator] 规则不合法，按未命中处理: %s", exc)
            spec = None

        if spec is not None:
            wanted = build_candidates(spec, identity=identity, args=args)
            if wanted:
                if visible is None:
                    visible = await self._visible_records(session, user_id, dept_code)
                hit, used = pick_match(spec, wanted, visible)
                if hit is not None:
                    logger.info(
                        "[SecretLocator] 规则命中 consumer=%s/%s slot=%s wanted=%s hit=%s",
                        consumer_type, consumer_id, slot, used, getattr(hit, "secret_key", "?"),
                    )
                    return Located(secret_id=getattr(hit, "id", None), strategy="rule")

        # —— 未命中：只看规则自己声明的策略，绝不默认回退
        on_miss = getattr(spec, "on_miss", "fail") if spec is not None else "fail"
        if on_miss == "fallback_fixed" and fixed_binding is not None and fixed_binding.secret_id:
            logger.info("[SecretLocator] 规则未命中，回退本层固定绑定 slot=%s", slot)
            return Located(
                secret_id=fixed_binding.secret_id, strategy="fixed", fallback_used=True
            )
        if on_miss == "model" and preferred_secret_id:
            if visible is None:
                visible = await self._visible_records(session, user_id, dept_code)
            if any(getattr(s, "id", None) == preferred_secret_id for s in visible):
                return Located(secret_id=preferred_secret_id, strategy="model_choice")
            logger.warning(
                "[SecretLocator] 模型选的凭据不在可见集合内，已拒绝 slot=%s choice=%s",
                slot, preferred_secret_id,
            )

        return Located(
            denial=DENY_RULE_MISS,
            detail=f"规则未命中（要找 {wanted[0] if wanted else spec.key_source}），可见候选 {len(visible or [])} 条",
        )

    return Located(denial=None)
```

**（已核准）denial 常量全部集中在 `app/services/secrets/policy.py`**（`DENY_BINDING` / `DENY_GRANT` / `DENY_HOST` / `DENY_SCENARIO` / `DENY_EXPIRED` / `DENY_DISABLED` 都在那儿），`resolver.py` 是从 policy 导入的。所以：

1. 在 `policy.py` 的 `DENY_DISABLED` 之后加：

```python
DENY_RULE_MISS = "rule_miss"
```

2. 在 `resolver.py` 现有的 `from app.services.secrets.policy import (...)` 列表里插入 `DENY_RULE_MISS,`（按字母序放在 `DENY_SCENARIO` 之前那一段）。

3. **不需要**加 `from typing import Any, Mapping, Sequence`：`resolver.py` 已有 `from __future__ import annotations`，注解是延迟求值，`Mapping[str, Any] | None` 这种写法不会在运行时解析。硬加反而会引入无用导入。

- [ ] **Step 4: 补 `_bindings_for_slot` 与 `_visible_records`**

`_bindings_for_slot` 是 `_pick_binding` 去掉 `limit(1)`、保留个人优先排序的版本（`_pick_binding` 暂时保留，P2 之后再决定是否删除，避免一次改动面过大）：

```python
@staticmethod
async def _bindings_for_slot(
    session,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    user_id: str,
) -> list[SecretBinding]:
    """取该槽位的全部候选绑定（本人的 + 全局的），个人优先、规则优先。"""
    stmt = (
        select(SecretBinding)
        .where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            or_(
                SecretBinding.bound_by_user_id.is_(None),
                SecretBinding.bound_by_user_id == "",
                SecretBinding.bound_by_user_id == user_id,
            ),
        )
        .order_by(
            and_(
                SecretBinding.bound_by_user_id.isnot(None),
                SecretBinding.bound_by_user_id != "",
            ).desc(),
            (SecretBinding.strategy == "rule").desc(),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


@staticmethod
async def _visible_records(session, user_id: str, dept_code: str | None) -> Sequence[Any]:
    """可见集合：本人的 + 被授权的系统级。规则只在这个集合里匹配。"""
    from app.services.secrets.center import list_visible_secrets

    return await list_visible_secrets(
        session, user_id=user_id, dept_code=dept_code, only_active=True
    )
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py -v
```

Expected: 7 passed。

- [ ] **Step 6: 检查点**

汇报：`_locate` 的层/策略顺序、两个新查询函数、7 条用例覆盖了哪些语义。

---

## Task 5: `resolve()` 接入 `_locate`

**Files:**
- Modify: `app/services/secrets/resolver.py`（`resolve` 方法体开头）
- Test: `tests/secrets/test_locator_resolve.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_resolve_reports_rule_miss_denial(monkeypatch):
    """规则未命中时 resolve 必须返回 rule_miss，且不碰任何凭据行。"""
    r = SecretResolver()

    async def fake_locate(*a, **kw):
        return Located(denial="rule_miss", detail="规则未命中")

    monkeypatch.setattr(SecretResolver, "_locate", fake_locate)
    result = await r.resolve(
        user_id="u1", scene="mcp", consumer_type="mcp_server",
        consumer_id="c", slot="arg:send_mail:api_key",
    )
    assert result.ok is False and result.denial == "rule_miss"
```

`Located` 需在测试文件顶部 `from app.services.secrets.resolver import Located, SecretResolver`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py::test_resolve_reports_rule_miss_denial -v
```

Expected: FAIL —— `denial` 是 `None` 或 `missing identity`（`resolve` 还没接 `_locate`）。

- [ ] **Step 3: 改 `resolve()`**

把现有的：

```python
                binding = await self._pick_binding(
                    session, consumer_type, consumer_id, slot, user_id
                )
                if binding is None:
                    # 没有绑定 = 零授权。这不是错误，是常态：绝大多数工具本来就不带凭据。
                    return ResolveResult(ok=False, denial=DENY_BINDING)

                row = await session.get(UserSecret, binding.secret_id)
                if row is None:
                    return ResolveResult(
                        ok=False, denial=DENY_BINDING, detail="secret row missing"
                    )
```

替换为：

```python
                located = await self._locate(
                    session,
                    consumer_type=consumer_type,
                    consumer_id=consumer_id,
                    slot=slot,
                    user_id=user_id,
                    dept_code=dept_code,
                    identity=user_attrs,
                    args=rule_args,
                    preferred_secret_id=preferred_secret_id,
                )
                if located.denial:
                    return ResolveResult(ok=False, denial=located.denial, detail=located.detail)
                if not located.secret_id:
                    # 没有绑定 = 零授权。这不是错误，是常态：绝大多数工具本来就不带凭据。
                    return ResolveResult(ok=False, denial=DENY_BINDING)

                row = await session.get(UserSecret, located.secret_id)
                if row is None:
                    return ResolveResult(
                        ok=False, denial=DENY_BINDING, detail="secret row missing"
                    )
```

同时在 `resolve` 签名末尾加三个带默认值的参数：

```python
        user_attrs: Mapping[str, Any] | None = None,
        rule_args: Mapping[str, Any] | None = None,
        preferred_secret_id: str | None = None,
```

- [ ] **Step 4: 让流水区分「回退命中」**

`_log_usage(... result=...)` 的所有调用点里，成功的那一处改成按定位结果分流。找到 `result=RESULT_SUCCESS`（或等价的成功常量），改成：

```python
                            result=(
                                RESULT_SUCCESS_FALLBACK
                                if located.fallback_used
                                else RESULT_SUCCESS
                            ),
```

并在常量区加：

```python
RESULT_SUCCESS_FALLBACK = "success_fallback"
```

**（已核准）该文件没有 `RESULT_SUCCESS` 常量**，成功流水是字面量 `result="success"`（`resolver.py:224`）。所以两步：

1. 在常量区（`DEFAULT_MAX_INJECT_PER_CALL` 附近）加：

```python
#: 规则未命中、按规则自己声明的策略回退到固定绑定时的流水结果。
#: 单独一个值是为了事后能查「规则其实一直没命中」。
RESULT_SUCCESS_FALLBACK = "success_fallback"
```

2. 把 `result="success",` 这一处替换成上面的三目表达式。

- [ ] **Step 5: 跑测试**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py tests/secrets/test_locator_rule.py -v
```

Expected: 全部 passed。

- [ ] **Step 6: 检查点**

汇报 `resolve` 的改动范围（只动开头 15 行 + 一处流水常量）；强调四道闸门代码未动。

---

## Task 6: 注入层透传三个参数

**Files:**
- Modify: `app/services/secrets/injection.py`（`_current_user` 旁 + 三个注入函数）
- Test: `tests/secrets/test_locator_resolve.py`（追加一条契约测试）

- [ ] **Step 1: 写失败测试**

```python
def test_injection_passes_identity_and_args_to_resolve():
    """规则要按「入参的值」匹配，注入层必须把入参与身份字段传进去。

    这里断言源码里三处调用都传了 rule_args / user_attrs——注入函数依赖真实
    会话与网络，用源码契约测试比造一整套 mock 更稳，也更能防止将来被改回去。
    """
    from pathlib import Path

    src = Path("app/services/secrets/injection.py").read_text(encoding="utf-8")
    assert src.count("rule_args=") >= 2, "参数级注入必须传 rule_args"
    # 已核准：injection.py 里有 **5 处** resolve 调用（228/308/421/547/711 行），
    # 对应 api_tool_bindings / api_tool_param_bindings / mcp_bindings /
    # mcp_arg_bindings / script_env_bindings 五个入口
    assert src.count("user_attrs=") >= 5, "五个注入入口都必须传 user_attrs"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py::test_injection_passes_identity_and_args_to_resolve -v
```

Expected: FAIL（计数为 0）。

- [ ] **Step 3: 加身份字段提取**

在 `_current_user` 旁边加：

```python
def _current_user_attrs(user_info: Mapping[str, Any] | None) -> dict[str, str]:
    """身份字段：身份类键来源（``user.email`` 等）从这里取值。

    只挑规则可能用到的几个，不把整个 user_info 塞进解析器——那会把
    token、权限等无关字段也带进日志上下文。
    """
    info = user_info or {}
    attrs: dict[str, str] = {}
    for key in ("email", "user_name", "username", "real_name", "dept_code", "user_id"):
        value = info.get(key)
        if isinstance(value, str) and value:
            attrs[key] = value
    # user_name 与 username 是历史双写，统一成规则里的名字
    if "user_name" not in attrs and "username" in attrs:
        attrs["user_name"] = attrs["username"]
    return attrs
```

- [ ] **Step 4: 三个注入函数传参**

`apply_mcp_arg_bindings`（参数级，有入参可用）：

```python
            result = await secret_resolver.resolve(
                user_id=user_id,
                scene=SCENE_MCP,
                consumer_type=CONSUMER_MCP,
                consumer_id=server_id,
                slot=slot,
                target_host=target,
                trace_id=trace_id,
                dept_code=dept_code,
                user_attrs=user_attrs,
                # 参数级注入时，模型填的其余参数就是规则的匹配来源。
                # 去掉凭据参数自身：用一条凭据去决定另一条凭据会形成依赖环。
                rule_args={k: v for k, v in (param_values or {}).items() if k != arg_name},
            )
```

在该函数开头取一次 `user_attrs = _current_user_attrs(user_info)`。

**（已核准变量名）** `apply_mcp_arg_bindings` 的签名是 `(tool_record, arguments, *, user_info, denials)`，循环里凭据参数名是 `name`、槽位是 `slot`。所以上面那段代码落地时是：

```python
                rule_args={k: v for k, v in arguments.items() if k != name},
```

`apply_api_tool_param_bindings` 的入参字典叫 `params`，那边用 `rule_args=dict(params)`,并把凭据参数名（该函数里是 `name`）排除掉：
```python
                rule_args={k: v for k, v in params.items() if k != name},
```
```

**（已核准）`injection.py` 里共有 5 处 `secret_resolver.resolve(` 调用**，改造清单如下，一处都不能漏：

| 行号 | 函数 | 加 `user_attrs` | 加 `rule_args` |
|---|---|---|---|
| 228 | `apply_api_tool_bindings`（API 工具 Header） | ✓ | ✗（Header 没有入参） |
| 308 | `apply_api_tool_param_bindings`（API 工具参数级） | ✓ | ✓ `{k: v for k, v in params.items() if k != name}` |
| 421 | `apply_mcp_bindings`（MCP Header） | ✓ | ✗ |
| 547 | `apply_mcp_arg_bindings`（MCP 参数级） | ✓ | ✓ `{k: v for k, v in arguments.items() if k != name}` |
| 711 | `apply_script_env_bindings`（脚本 env） | ✓ | ✗（env 无入参；规则只能用身份字段，够用） |

每处函数开头补一行 `user_attrs = _current_user_attrs(user_info)`。

- [ ] **Step 5: 跑测试**

```bash
.venv/bin/python -m pytest tests/secrets/ -v
```

Expected: 全部 passed。

- [ ] **Step 6: 检查点**

汇报：改了哪几个 `resolve` 调用点、`rule_args` 只在参数级注入里传（Header 没有入参）。

---

## Task 7: 接口支持规则绑定与试算

**Files:**
- Modify: `app/services/secrets/center.py`（新增 `bind_rule`）
- Modify: `app/api/portal/endpoints/secrets.py`（`BindRequest`、`POST /bindings`、`GET /bindings`、新增 `POST /locate-preview`）
- Test: `tests/secrets/test_locator_resolve.py`（追加端点契约测试）

- [ ] **Step 1: 写失败测试（源码契约，轻量但能挡住回退）**

```python
def test_bindings_endpoint_uses_left_join_for_rule_rows():
    """规则绑定没有 secret_id，INNER JOIN 会让它整行消失。"""
    from pathlib import Path

    src = Path("app/api/portal/endpoints/secrets.py").read_text(encoding="utf-8")
    assert "outerjoin(UserSecret" in src, "按消费者查绑定时必须用 outerjoin，否则规则行查不出来"
    assert '"strategy"' in src and "rule_summary" in src


def test_locate_preview_endpoint_exists_and_uses_current_user():
    from pathlib import Path

    src = Path("app/api/portal/endpoints/secrets.py").read_text(encoding="utf-8")
    assert '@router.post("/locate-preview"' in src
    # 试算固定用当前登录人身份（已拍板），不接受指定用户
    assert "sample_user_id" not in src
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py -k "left_join or locate_preview" -v
```

Expected: 两条 FAIL。

- [ ] **Step 3: `center.bind_rule()`**

```python
async def bind_rule(
    session,
    *,
    consumer_type: str,
    consumer_id: str,
    slot: str,
    rule: dict,
    bound_by_user_id: str | None,
    dept_code: str | None = None,
) -> SecretBinding:
    """把一个槽位绑到「一条定位规则」上（``strategy='rule'``，无 secret_id）。

    与 ``bind_secret`` 分开而不是加参数：固定绑定要校验 secret 归属与授权，
    规则绑定没有 secret 可校验——混在一个函数里会让两条校验路径互相纠缠。
    """
    from app.services.secrets.locator import RuleError, parse_rule

    try:
        spec = parse_rule(rule)
    except RuleError as exc:
        raise SecretValidationError(str(exc)) from exc

    owner = bound_by_user_id or ""
    existing = (
        await session.execute(
            select(SecretBinding).where(
                SecretBinding.consumer_type == consumer_type,
                SecretBinding.consumer_id == consumer_id,
                SecretBinding.slot == slot,
                SecretBinding.bound_by_user_id == owner,
                SecretBinding.strategy == "rule",
            )
        )
    ).scalars().first()
    payload = json.dumps(
        {
            "key_source": spec.key_source,
            "transforms": list(spec.transforms),
            "templates": list(spec.templates),
            "target": spec.target,
            "match": spec.match,
            "on_miss": spec.on_miss,
        },
        ensure_ascii=False,
    )
    if existing is not None:
        existing.rule_json = payload
        existing.secret_id = None
        await session.commit()
        return existing

    binding = SecretBinding(
        id=str(uuid.uuid4()),
        secret_id=None,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        slot=slot,
        bound_by_user_id=owner,
        strategy="rule",
        rule_json=payload,
    )
    session.add(binding)
    await session.commit()
    return binding
```

（`json` / `uuid` / `select` / `SecretBinding` / `SecretValidationError` 在 `center.py` 里应已存在；缺什么补什么。）

- [ ] **Step 4: `POST /bindings` 支持 `strategy`**

**（已核准）`BindRequest` 现在是这样**（`app/api/portal/endpoints/secrets.py:79`）：

```python
class BindRequest(BaseModel):
    secret_id: str          # ← 必填！规则绑定没有 secret_id，必须改成可选
    consumer_type: str
    consumer_id: str
    slot: str = ""
    name: str = ""
    is_global: bool = False
```

改成：

```python
class BindRequest(BaseModel):
    #: strategy=fixed 时必填；strategy=rule 时留空
    secret_id: str = ""
    consumer_type: str
    consumer_id: str
    slot: str = ""
    name: str = ""
    is_global: bool = False
    #: fixed（绑一条凭据）/ rule（绑一条定位规则）
    strategy: str = "fixed"
    #: strategy=rule 时必填，形如 {"key_source": "arg:to", ...}
    rule: dict | None = None
```

同时在 `create_binding` 里补两道校验（放在 `_resolve_slot` 之前）：

```python
    if payload.strategy not in ("fixed", "rule"):
        raise HTTPException(status_code=400, detail="strategy 只能是 fixed 或 rule")
    if payload.strategy == "fixed" and not payload.secret_id:
        raise HTTPException(status_code=400, detail="固定绑定必须指定 secret_id")
```

`create_binding` 开头分流（在现有 `slot` 解析之后、`is_global` 判断之后）：

```python
    if payload.strategy == "rule":
        if payload.rule is None:
            raise HTTPException(status_code=400, detail="strategy=rule 时必须给出 rule")
        # 非管理员只能在个人层配规则（bound_by_user_id = 自己）
        try:
            binding = await center.bind_rule(
                db,
                consumer_type=payload.consumer_type,
                consumer_id=payload.consumer_id,
                slot=slot,
                rule=payload.rule,
                bound_by_user_id=bound_by,
            )
        except SecretValidationError as exc:
            raise _fail(exc)
        return {
            "code": 0,
            "message": "已保存规则",
            "data": {"id": binding.id, "slot": binding.slot, "strategy": "rule"},
        }
```

`strategy` 只接受 `fixed` / `rule`，其它值 400。`strategy=fixed` 时沿用现有 `bind_secret` 路径。

**（已核准）解绑也要区分策略，否则规则行删不掉、或误删固定行。** `UnbindRequest`（同文件）现在是：

```python
class UnbindRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str = ""
    name: str = ""
    is_global: bool = False
```

加一个字段并在 `DELETE /bindings` 的删除条件里带上：

```python
class UnbindRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str = ""
    name: str = ""
    is_global: bool = False
    #: 解绑哪一条：fixed / rule。默认 fixed —— 存量调用方都不会传，行为不变
    strategy: str = "fixed"
```

**（已核准）解绑函数叫 `center.unbind_slot`（`center.py:461`），不是 `unbind_secret`，而且它的删除条件不含 `strategy`：**

```python
    result = await session.execute(
        delete(SecretBinding).where(
            SecretBinding.consumer_type == consumer_type,
            SecretBinding.consumer_id == consumer_id,
            SecretBinding.slot == slot,
            owner_clause,          # ← 只到 bound_by_user_id，没有 strategy
        )
    )
```

后果：规则行上线后，用户点一次「解绑」会把同层的**规则行和固定行一起删掉**（同一 `(consumer, slot, owner)` 现在有两条）。

改法：给 `unbind_slot` 加一个**默认 `"fixed"` 的 `strategy` 参数**（保持所有既有调用方行为不变），并在上面两处 `where`（取 `secret_id` 的那次 SELECT 和 DELETE）里都加上：

```python
            SecretBinding.strategy == strategy,
```

审计里「解绑的是哪条凭据」的 `target = select(SecretBinding.secret_id)` 在解规则行时会拿到 `None`（规则没有 secret_id），审计文案要能说明「解绑的是一条规则」，按现有 `_audit` 的入参补一句即可，不要因此跳过审计。

**同文件另外三处删除不受影响**（已核对，不要多改）：
- `center.py:687` 删凭据时按 `secret_id` 级联删绑定——规则行 `secret_id` 为 NULL，删不到，表现为「规则仍在但匹配不到东西 → 未命中」，可接受；
- `center.py:1067` 删消费者时按 `(consumer_type, consumer_id)` 删全部绑定——规则行会一起删，正确；
- `secrets.py:995` 的 `secret_id.in_(...)` 查询——规则行为 NULL，不会被误查，正确。

- [ ] **Step 5: `GET /bindings` 改 LEFT JOIN**

把

```python
    stmt = (
        select(SecretBinding, UserSecret)
        .join(UserSecret, UserSecret.id == SecretBinding.secret_id)
    )
```

改成

```python
    stmt = (
        select(SecretBinding, UserSecret)
        .outerjoin(UserSecret, UserSecret.id == SecretBinding.secret_id)
    )
```

并把返回项补上（`secret` 可能为 None）：

```python
            {
                "id": binding.id,
                "slot": binding.slot,
                "strategy": getattr(binding, "strategy", None) or "fixed",
                "secret_id": binding.secret_id,
                "secret_key": secret.secret_key if secret else None,
                "secret_name": secret.name if secret else None,
                "owner_scope": secret.owner_scope if secret else None,
                "secret_status": secret.status if secret else None,
                "secret_expires_at": (
                    secret.expires_at.isoformat() if secret and secret.expires_at else None
                ),
                "rule": _rule_payload(binding),
                "rule_summary": _rule_summary_of(binding),
                "consumer_type": binding.consumer_type,
                "consumer_id": binding.consumer_id,
                "bound_by_me": binding.bound_by_user_id == user_id,
            }
```

同文件加两个小工具（读坏 JSON 时返回 None，不抛）：

```python
def _rule_payload(binding) -> dict | None:
    raw = getattr(binding, "rule_json", None)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _rule_summary_of(binding) -> str | None:
    from app.services.secrets.locator import RuleError, parse_rule, rule_summary

    raw = getattr(binding, "rule_json", None)
    if not raw:
        return None
    try:
        return rule_summary(parse_rule(raw))
    except RuleError:
        return "规则不合法（需重新配置）"
```

- [ ] **Step 6: 新增 `POST /locate-preview`**

```python
class LocatePreviewRequest(BaseModel):
    consumer_type: str
    consumer_id: str
    slot: str
    rule: dict
    sample_args: dict[str, Any] = Field(default_factory=dict)


@router.post("/locate-preview", summary="试算：这条规则会命中哪条凭据（不发请求、不解密）")
async def locate_preview(
    payload: LocatePreviewRequest,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    """只算「会命中谁」：身份固定为**当前登录人**，不写流水、不写审计。

    管理员配全局规则时，试算结果只代表他自己的可见范围——其他人被授权的
    凭据可能不同，界面要写明这一点。
    """
    from app.services.secrets.locator import (
        RuleError,
        build_candidates,
        parse_rule,
        pick_match,
        rule_summary,
    )

    try:
        spec = parse_rule(payload.rule)
    except RuleError as exc:
        raise _fail(exc)

    user_id, dept_code = _user_ctx(user_info)
    identity = _identity_attrs(user_info)
    steps: list[dict[str, Any]] = []
    wanted = build_candidates(spec, identity=identity, args=payload.sample_args)
    steps.append({"step": "取值", "detail": f"{spec.key_source} → {wanted[0] if wanted else '（空）'}"})
    steps.append({"step": "套模板", "detail": " → ".join(wanted) or "（无）"})

    visible = await center.list_visible_secrets(
        db, user_id=user_id, dept_code=dept_code, only_active=True
    )
    steps.append({"step": "可见凭据", "detail": f"{len(visible)} 条"})
    hit, used = pick_match(spec, wanted, visible)
    if hit is not None:
        steps.append({"step": "匹配", "detail": f"命中 {hit.secret_key}"})
        return {
            "code": 0,
            "data": {
                "ok": True,
                "strategy": "rule",
                "secret_key": hit.secret_key,
                "secret_name": hit.name,
                "on_miss": spec.on_miss,
                "summary": rule_summary(spec),
                "candidate_count": len(visible),
                "steps": steps,
                "miss_reason": None,
            },
        }

    steps.append({"step": "匹配", "detail": "未命中"})
    return {
        "code": 0,
        "data": {
            "ok": False,
            "strategy": "rule",
            "secret_key": None,
            "secret_name": None,
            "on_miss": spec.on_miss,
            "summary": rule_summary(spec),
            "candidate_count": len(visible),
            "steps": steps,
            "miss_reason": (
                f"没有 {spec.target} = {wanted[0]} 的凭据"
                if wanted
                else f"取不到 {spec.key_source} 的值"
            ),
            "miss_behaviour": {
                "fail": "这次调用会失败",
                "fallback_fixed": "会回退到同层的固定凭据",
                "model": "会让模型在可见候选里挑一条",
            }[spec.on_miss],
        },
    }
```

`_identity_attrs(user_info)` 同 `injection._current_user_attrs` 逻辑，放在本文件私有函数里（避免端点依赖注入模块的私有函数）。

- [ ] **Step 7: 跑测试**

```bash
.venv/bin/python -m pytest tests/secrets/ -v
```

Expected: 全部 passed。

- [ ] **Step 8: 检查点**

汇报三个接口改动（POST /bindings 分流、GET /bindings 改 LEFT JOIN、新增 locate-preview），说明「试算不写流水、不解密」。

---

## Task 8: 变异验证（证明测试真的能挡住回退）

**Files:**
- Modify: 临时改动 `app/services/secrets/resolver.py` / `secrets.py`（跑完立刻还原）

- [ ] **Step 1: 变异 1 —— 让 `on_miss=fail` 回退到固定绑定**

把 `_locate` 里 `on_miss == "fallback_fixed"` 改成 `on_miss in ("fallback_fixed", "fail")`，然后跑：

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py -v
```

Expected: **FAIL** —— `test_rule_miss_with_fail_does_not_fall_through_to_global_fixed` 必须红。红了说明测试有效，立刻还原。

- [ ] **Step 2: 变异 2 —— 把 `GET /bindings` 的 outerjoin 改回 join**

```bash
.venv/bin/python -m pytest tests/secrets/test_locator_resolve.py -k left_join -v
```

Expected: **FAIL**。红了立刻还原。

- [ ] **Step 3: 变异 3 —— 让规则在「全表」而不是「可见集合」里匹配**

把 `_visible_records` 改成返回 `select(UserSecret)` 全表（临时），跑：

```bash
.venv/bin/python -m pytest tests/secrets/ -v
```

Expected: 至少一条 **FAIL**（若没有红，说明「越权防线」缺测试，**补一条再继续**：构造一条别人私有凭据在「全表」里存在、`visible` 里不存在的用例）。

还原改动。

- [ ] **Step 4: 确认三处都已还原**

```bash
git diff --stat app/services/secrets/resolver.py app/api/portal/endpoints/secrets.py
```

Expected: 只剩本计划 Task 4/5/7 的正常改动，没有变异残留。

- [ ] **Step 5: 检查点**

汇报三次变异各自的「红的用例名」。

---

## Task 9: 收尾

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 跑相关回归**

```bash
.venv/bin/python -m pytest tests/secrets/ tests/test_secret_policy_config_validation.py -v
.venv/bin/python -m pytest tests/frontend -q --confcutdir=tests/frontend
```

Expected: 新增用例全绿；前端契约测试数量与改动前一致（本阶段没动前端）。

- [ ] **Step 2: 更新 `tests/CHECKLIST.md`**

在表格里新增一行（放在最新一行之后）：需求 = 「凭据定位规则 P1：规则内核与试算」；文件 = 本计划新增/修改的文件；说明 = 迁移编号、`_locate` 顺序、`on_miss` 语义、四道闸门未动、三项变异验证；状态 = 「✅ P1 完成（待实机）」；日期 = `2026-11-10`。列的写法照表格里已有行（5 列）。

- [ ] **Step 3: 检查点（交付汇报）**

汇报内容：

1. 改动文件清单（新增/修改分开）
2. 测试结果：新增用例数、通过情况
3. 三项变异验证各自红的用例
4. **需要用户在控制台做的事**：执行 `db-prod/V211`、`V212`（以及 PG 的 `V111`、`V112`）
5. 已知限制：P1 只到接口层，界面还没有「按规则匹配」选项（P2）；`on_miss=model` 的候选注入尚未实现（P3），现在传了 `preferred_secret_id` 但没人传
6. 提醒不提交，由用户决定

---

## 自审记录

**规格覆盖检查**（逐节对回任务）：

| 规格 | 任务 |
|---|---|
| §5 规则表达 `rule_json` | Task 3（`parse_rule` 的字段白名单与枚举） |
| §5.1 解析顺序（层间个人优先、层内规则优先、fail 不越层） | Task 4 |
| §5.2 规则挂在谁的绑定上 / 非管理员不能写全局 | Task 7（`create_binding` 里 `is_global` 判断沿用现有代码） |
| §6 双库迁移 + 先加后删 | Task 1 |
| §7.1 `_pick_binding` → `_locate`、可见集合内匹配 | Task 4 |
| §7.2 两条链路的失败语义 | Task 5（denial 向上传递；Header 的「不阻断」由现有 `apply_mcp_bindings` 的 continue 保证，无需改） |
| §7.3 试算端点、身份固定当前登录人 | Task 7 Step 6 |
| §7.4 模型兜底 | **不在 P1**（P3）；P1 只预留 `preferred_secret_id` 通道 |
| §8.5 `list_my_credentials` | **不在 P1**（P3） |
| §9 前端 | **不在 P1**（P2） |
| §10 审计（`success_fallback`、定位日志） | Task 4 Step 3（logger.info）+ Task 5 Step 4 |
| §12 测试计划（后端部分） | Task 3/4/5/6/7 的用例 + Task 8 变异验证 |
| §14 P1 定义 | 本计划整体 |

**占位符扫描：** 无 TBD / TODO / 「类似 Task N」/「补充错误处理」。每个代码步骤都给了可直接粘贴的实现。

**类型一致性：** `Located(secret_id, strategy, fallback_used, denial, detail)` 在 Task 4 定义，Task 5 测试与实现、Task 4 的 7 条用例都只用这五个字段；`_locate` 的关键字参数名（`rows` / `visible` / `preferred_secret_id` / `identity` / `args`）在测试与实现中一致；`DENY_RULE_MISS = "rule_miss"` 在 Task 4 定义，Task 5 测试断言同一个字符串。

**已知的实现风险（执行时若撞上，停下来汇报而不是自行改设计）：**

1. `resolve()` 里成功流水的常量名可能不是 `RESULT_SUCCESS`（Task 5 Step 4 已说明按实际替换）。
2. `center.bind_secret` 内部可能有「同槽位已有绑定则更新」的逻辑，`bind_rule` 写的是**独立的 strategy 维度**，两者不冲突但要注意：同一层现在可以各有一条。
3. `apply_mcp_arg_bindings` 里「模型填的其余参数」变量名可能是 `params` 或 `arguments`，按实际取。
