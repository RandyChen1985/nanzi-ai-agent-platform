# Langfuse LLM 链路追踪接入（一期）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让平台具备 Langfuse 链路追踪能力：独立配置表 + 独立系统设置 Tab + AgentScope Agent 链路自动上报，全程旁路、可热开关、可降级。

**Architecture:** 新增 `app/services/ai/observability/` 包：`config_store.py`（独立表读写与校验）、`credentials.py`（Fernet 加密）、`settings.py`（进程内同步快照 + 30s 刷新）、`gate.py`（开关与 trace 粒度采样）、`span_attributes.py`（gen_ai/langfuse 属性构造）、`manager.py`（Langfuse SDK 初始化、导出钩子、热重建、降级）。Agent 链路靠 AgentScope 官方 `TracingMiddleware` 自动出 span，平台只补一个属性增强中间件与一个轮次根 span。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.x async / AgentScope 2.0.9 / `langfuse`（锁精确版本）/ OpenTelemetry SDK（AgentScope 已带）/ Vue 3 + TS + Tailwind。

**上游设计文档：** [`docs/superpowers/specs/2026-09-30-langfuse-llm-tracing-design.md`](../specs/2026-09-30-langfuse-llm-tracing-design.md)

---

## 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `requirements.txt` | 改 | 新增 `langfuse==<精确版本>` + 锁版本注释 |
| `app/services/ai/observability/__init__.py` | 建 | 包导出 |
| `app/services/ai/observability/credentials.py` | 建 | `langfusekey:v1:` 前缀的加解密 |
| `app/services/ai/observability/gate.py` | 建 | 开关/采样决策（trace 粒度） |
| `app/services/ai/observability/config_store.py` | 建 | `langfuse_config` 表读写 + 字段校验 |
| `app/services/ai/observability/span_attributes.py` | 建 | span 属性构造、usage 提取、内容属性清单 |
| `app/services/ai/observability/settings.py` | 建 | 同步快照 + 后台刷新 |
| `app/services/ai/observability/manager.py` | 建 | SDK 生命周期、导出钩子、热重建、连通性探测 |
| `app/services/ai/observability/context_middleware.py` | 建 | `LangfuseContextMiddleware`（补平台身份属性） |
| `app/services/ai/observability/turn_span.py` | 建 | 轮次根 span 的创建/结束 |
| `app/services/ai/runtime/agentscope/agent_runtime.py` | 改 | 中间件列表追加 Tracing 链路 |
| `app/services/ai/pipeline/runner.py` | 改 | `run()` 拆为薄包装 + `_run_steps()`，包根 span |
| `app/main.py` | 改 | lifespan 初始化/关闭 |
| `app/api/portal/endpoints/langfuse.py` | 建 | GET/PUT/`test` 三个端点 |
| `app/api/portal/api.py` | 改 | 注册 `/system/langfuse` |
| `db-prod/V161-create-langfuse-config.sql` | 建 | 建表 + 默认行 + 权限元素 |
| `db-prod-pg/V62-create-langfuse-config.sql` | 建 | 同上（PG 语法） |
| `frontend/src/views/SystemConfig.vue` | 改 | 新增 Langfuse Tab |
| `frontend/src/components/system/LangfuseConfig.vue` | 建 | Tab 面板 |
| `frontend/src/constants/permissions.ts` | 改 | 登记 `element:system:langfuse_save` |
| `tests/services/ai/observability/*.py` | 建 | 单元测试 |
| `tests/api/portal/test_langfuse_config_api.py` | 建 | 接口测试 |
| `tests/test_langfuse_migration_contract.py` | 建 | 迁移契约 |
| `tests/frontend/test_langfuse_config_contract.py` | 建 | 前端契约 |
| `tests/CHECKLIST.md` | 改 | 登记新测试 |

---

### Task 1: 引入依赖并确认基线

**Files:** Modify `requirements.txt`

- [ ] **Step 1:** 查询并锁定版本：`.venv/bin/python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/langfuse/json'))['info']['version'])"`，把结果写死进 `requirements.txt`（追加在 `agentscope[...]==2.0.9` 之后），并加注释说明「升级前需核对 docs/superpowers/specs/2026-09-30-langfuse-llm-tracing-design.md 中引用的属性名与钩子行为」。
- [ ] **Step 2:** 安装：`.venv/bin/pip install -r requirements.txt`
- [ ] **Step 3:** 验证导入：`.venv/bin/python -c "import langfuse; print(langfuse.__version__)"` → 打印版本号
- [ ] **Step 4:** 基线回归：`PYTHONPATH=. .venv/bin/python -m pytest tests/test_db_prod_apply_sql.py tests/test_db_prod_pg_apply_sql.py -q` → 全绿

---

### Task 2: `credentials.py` 密钥加解密

**Files:** Create `app/services/ai/observability/__init__.py`, `app/services/ai/observability/credentials.py`; Test `tests/services/ai/observability/test_credentials.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from app.services.ai.observability import credentials


def test_encrypt_returns_prefixed_ciphertext():
    stored = credentials.encrypt_langfuse_secret("sk-lf-abc")
    assert stored.startswith(credentials.SECRET_PREFIX)
    assert "sk-lf-abc" not in stored
    assert credentials.decrypt_langfuse_secret(stored) == "sk-lf-abc"


def test_empty_secret_roundtrips_as_none():
    assert credentials.encrypt_langfuse_secret("") is None
    assert credentials.encrypt_langfuse_secret(None) is None
    assert credentials.decrypt_langfuse_secret(None) is None
    assert credentials.decrypt_langfuse_secret("   ") is None


def test_legacy_plaintext_is_accepted():
    assert credentials.decrypt_langfuse_secret("sk-lf-legacy") == "sk-lf-legacy"


def test_broken_ciphertext_raises():
    broken = credentials.SECRET_PREFIX + "not-a-valid-fernet-token"
    with pytest.raises(credentials.LangfuseCredentialError):
        credentials.decrypt_langfuse_secret(broken)
```

- [ ] **Step 2: 跑测试确认失败** — `PYTHONPATH=. .venv/bin/python -m pytest tests/services/ai/observability/test_credentials.py -q` → `ModuleNotFoundError` / `AttributeError`
- [ ] **Step 3: 实现** — `SECRET_PREFIX = "langfusekey:v1:"`；`LangfuseCredentialError(ValueError)`；加密用 `get_api_key_manager().encrypt_api_key()`；解密时前缀存在则解密并把 `ValueError` 包成 `LangfuseCredentialError`，无前缀则原样返回（兼容手工写库）。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5:** 暂不提交（按项目规范由用户统一提交）

---

### Task 3: `gate.py` 开关与采样

**Files:** Create `app/services/ai/observability/gate.py`; Test `tests/services/ai/observability/test_gate.py`

- [ ] **Step 1: 写失败测试**

```python
from app.services.ai.observability import gate


def test_rate_zero_never_samples():
    assert gate.is_sampled("trace-a", 0.0) is False


def test_rate_one_always_samples():
    assert gate.is_sampled("trace-a", 1.0) is True


def test_same_trace_is_stable_across_calls():
    results = {gate.is_sampled("trace-fixed", 0.5) for _ in range(50)}
    assert len(results) == 1


def test_distribution_is_roughly_proportional():
    sampled = sum(gate.is_sampled(f"trace-{i}", 0.25) for i in range(4000))
    assert 800 < sampled < 1200


def test_invalid_rate_falls_back_to_disabled():
    assert gate.is_sampled("trace-a", -1.0) is False
    assert gate.is_sampled("trace-a", 2.0) is False
    assert gate.is_sampled("trace-a", float("nan")) is False
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — `is_sampled(trace_id, sample_rate)`：非法 rate（非有限数或不在 [0,1]）返回 False；rate≥1 返回 True；rate≤0 返回 False；否则 `int.from_bytes(sha256(f"{trace_id}".encode()).digest()[:8], "big") / 2**64 < rate`。
- [ ] **Step 4: 跑测试确认通过**

---

### Task 4: `config_store.py` 单行配置表

**Files:** Create `app/services/ai/observability/config_store.py`; Test `tests/services/ai/observability/test_config_store.py`、`tests/services/ai/observability/test_config_store_db.py`

- [ ] **Step 1: 写纯逻辑失败测试**（不依赖数据库，用 `pytestmark = pytest.mark.no_infrastructure`）

```python
import pytest
from app.services.ai.observability import config_store

pytestmark = pytest.mark.no_infrastructure


def test_normalize_applies_defaults_for_missing_values():
    row = config_store.normalize_payload({})
    assert row["enabled"] is False
    assert row["sample_rate"] == 1.0
    assert row["capture_content"] is True
    assert row["timeout_seconds"] == 5
    assert row["host"] is None


def test_normalize_coerces_types():
    row = config_store.normalize_payload(
        {"enabled": "true", "sample_rate": "0.25", "capture_content": "false",
         "timeout_seconds": "7", "host": " http://lf:3000 "}
    )
    assert row["enabled"] is True
    assert row["sample_rate"] == 0.25
    assert row["capture_content"] is False
    assert row["timeout_seconds"] == 7
    assert row["host"] == "http://lf:3000"


@pytest.mark.parametrize("payload", [
    {"host": "lf:3000"},
    {"sample_rate": "1.5"},
    {"sample_rate": "abc"},
    {"timeout_seconds": "0"},
    {"enabled": "maybe"},
    {"environment": "x" * 65},
])
def test_normalize_rejects_invalid_values(payload):
    with pytest.raises(config_store.LangfuseConfigError):
        config_store.normalize_payload(payload)


def test_normalize_does_not_touch_secret_key():
    row = config_store.normalize_payload({"secret_key": "sk-lf-1"})
    assert row["secret_key"] == "sk-lf-1"
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** `normalize_payload`：

```python
DEFAULTS = {
    "enabled": False, "host": None, "public_key": None, "secret_key": None,
    "sample_rate": 1.0, "capture_content": True, "environment": None,
    "release": None, "timeout_seconds": 5, "trace_url_template": None,
}
_BOOL_FIELDS = ("enabled", "capture_content")
_STR_MAX = {"host": 512, "public_key": 255, "environment": 64, "release": 64,
            "trace_url_template": 512}
```
布尔接受 `True/False/"true"/"false"/1/0`，其余抛 `LangfuseConfigError`；`sample_rate` 转 float 且须 `0<=x<=1`；`timeout_seconds` 转 int 且 `>=1`；字符串字段去空白、空串转 `None`、超长抛错；`host` 非空时须以 `http://` 或 `https://` 开头。`secret_key` 原样保留（由调用方决定是否加密）。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 写数据库往返失败测试**（需要基础设施，不加 marker）

```python
import pytest
from app.services.ai.observability import config_store

ROW_ID = 1


@pytest.fixture(autouse=True)
async def _clean():
    await config_store.delete_config_for_tests()
    yield
    await config_store.delete_config_for_tests()


async def test_load_returns_defaults_when_row_missing():
    row = await config_store.load_config()
    assert row["enabled"] is False


async def test_save_then_load_roundtrip_encrypts_secret():
    await config_store.save_config(
        {"enabled": True, "host": "http://lf:3000", "public_key": "pk",
         "secret_key": "sk-lf-secret", "sample_rate": 0.5},
        changed_by="tester",
    )
    raw = await config_store.load_raw_row()
    assert raw["secret_key"].startswith("langfusekey:v1:")
    loaded = await config_store.load_config()
    assert loaded["secret_key"] == "sk-lf-secret"
    assert loaded["updated_by"] == "tester"


async def test_save_without_secret_key_keeps_previous_secret():
    await config_store.save_config({"secret_key": "sk-lf-1"}, changed_by="a")
    await config_store.save_config({"host": "http://lf:3000"}, changed_by="b")
    assert (await config_store.load_config())["secret_key"] == "sk-lf-1"


async def test_save_invalid_payload_does_not_write():
    with pytest.raises(config_store.LangfuseConfigError):
        await config_store.save_config({"sample_rate": 9}, changed_by="a")
    assert (await config_store.load_raw_row()) is None
```

- [ ] **Step 6: 跑测试确认失败** → `AttributeError`
- [ ] **Step 7: 实现 DB 部分** — SQLAlchemy `text()` 直写（与其他服务一致）：`load_raw_row()` 查 `langfuse_config WHERE id=1`；`load_config()` = `load_raw_row()` 缺失时返回 `normalize_payload({})`，存在时解密 `secret_key`（解密失败 → 抛错，由 `settings.py` 捕获置 `degraded`）；`save_config(payload, changed_by)`：`normalize_payload` → `secret_key` 出现时加密 → upsert（MySQL `ON DUPLICATE KEY UPDATE` / PG `ON CONFLICT (id) DO UPDATE`，用 `settings.normalized_database_type` 分支）；`delete_config_for_tests()` 辅助（仅测试用）。
- [ ] **Step 8: 跑测试确认通过**

---

### Task 5: `span_attributes.py`

**Files:** Create `app/services/ai/observability/span_attributes.py`; Test `tests/services/ai/observability/test_span_attributes.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from app.services.ai.observability import span_attributes as sa

pytestmark = pytest.mark.no_infrastructure

CONTENT_ATTRS = {
    "gen_ai.input.messages", "gen_ai.output.messages",
    "gen_ai.tool.call.arguments", "gen_ai.tool.call.result",
    "langfuse.observation.input", "langfuse.observation.output",
}


def test_trace_attributes_carry_platform_identity():
    attrs = sa.trace_attributes(
        user_id="u1", conversation_id="c1", agent_name="助手",
        trace_id="t1", extra={"agent_id": "a1"},
    )
    assert attrs["user.id"] == "u1"
    assert attrs["session.id"] == "c1"
    assert attrs["langfuse.trace.name"] == "助手 对话"
    assert attrs["langfuse.trace.metadata.trace_id"] == "t1"
    assert attrs["langfuse.trace.metadata.agent_id"] == "a1"


def test_trace_attributes_skip_empty_values():
    attrs = sa.trace_attributes(user_id=None, conversation_id=None,
                                agent_name=None, trace_id="t1", extra={})
    assert "user.id" not in attrs
    assert "session.id" not in attrs


def test_content_attribute_names_are_complete():
    assert set(sa.CONTENT_ATTRIBUTES) == CONTENT_ATTRS


def test_usage_extraction_from_dict_and_object():
    assert sa.extract_usage({"input_tokens": 3, "output_tokens": 4}) == (3, 4)
    class U:  # noqa: N801
        input_tokens = 5
        output_tokens = 6
    assert sa.extract_usage(U()) == (5, 6)
    assert sa.extract_usage(None) == (None, None)


def test_generation_attributes_use_genai_conventions():
    attrs = sa.generation_attributes(
        model="deepseek-chat", provider="deepseek", input_messages=[{"role": "user"}],
        output_messages=[{"role": "assistant"}], usage=(3, 4),
    )
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.request.model"] == "deepseek-chat"
    assert attrs["gen_ai.usage.input_tokens"] == 3
    assert attrs["gen_ai.usage.output_tokens"] == 4
    assert isinstance(attrs["gen_ai.input.messages"], str)
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — 属性名常量、`trace_attributes(...)`、`generation_attributes(...)`（消息用 `json.dumps(..., ensure_ascii=False)`）、`extract_usage(obj)`（兼容 dict / 对象 / `usage.input_tokens` 命名）、`CONTENT_ATTRIBUTES` 元组。
- [ ] **Step 4: 跑测试确认通过**

---

### Task 6: `settings.py` 快照与刷新

**Files:** Create `app/services/ai/observability/settings.py`; Test `tests/services/ai/observability/test_settings.py`

- [ ] **Step 1: 写失败测试**（`no_infrastructure`，monkeypatch `config_store.load_config`）

```python
import pytest
from app.services.ai.observability import settings as obs_settings

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _reset():
    obs_settings.reset_for_tests()
    yield
    obs_settings.reset_for_tests()


async def test_refresh_populates_snapshot(monkeypatch):
    async def fake_load():
        return {"enabled": True, "host": "http://lf:3000", "public_key": "pk",
                "secret_key": "sk", "sample_rate": 0.5, "capture_content": False,
                "environment": "prod", "release": None, "timeout_seconds": 5,
                "trace_url_template": None}
    monkeypatch.setattr(obs_settings.config_store, "load_config", fake_load)
    await obs_settings.refresh_snapshot()
    snap = obs_settings.get_snapshot()
    assert snap.enabled is True and snap.sample_rate == 0.5
    assert snap.capture_content is False
    assert snap.status == "enabled"


async def test_snapshot_reports_not_configured(monkeypatch):
    async def fake_load():
        return {"enabled": True, "host": None, "public_key": None, "secret_key": None,
                "sample_rate": 1.0, "capture_content": True, "environment": None,
                "release": None, "timeout_seconds": 5, "trace_url_template": None}
    monkeypatch.setattr(obs_settings.config_store, "load_config", fake_load)
    await obs_settings.refresh_snapshot()
    assert obs_settings.get_snapshot().status == "not_configured"


async def test_refresh_failure_keeps_previous_snapshot(monkeypatch):
    async def ok_load():
        return {"enabled": False, "host": None, "public_key": None, "secret_key": None,
                "sample_rate": 1.0, "capture_content": True, "environment": None,
                "release": None, "timeout_seconds": 5, "trace_url_template": None}
    monkeypatch.setattr(obs_settings.config_store, "load_config", ok_load)
    await obs_settings.refresh_snapshot()

    async def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(obs_settings.config_store, "load_config", boom)
    await obs_settings.refresh_snapshot()  # 不抛异常
    assert obs_settings.get_snapshot().enabled is False


def test_default_snapshot_is_disabled():
    assert obs_settings.get_snapshot().enabled is False
    assert obs_settings.get_snapshot().status == "disabled"
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — 冻结 dataclass `LangfuseSnapshot(enabled, host, public_key, secret_key, sample_rate, capture_content, environment, release, timeout_seconds, trace_url_template, status, client_fingerprint, last_error)`；`status` 取值 `disabled` / `not_configured` / `enabled` / `degraded`；`client_fingerprint = sha256(f"{host}|{public_key}|{secret_key}|{timeout_seconds}")`；全局 `_snapshot` 由 `threading.Lock` 保护；`refresh_snapshot()` 捕获一切异常并保留旧快照；`start_refresh_task()` 起 30s 循环的 asyncio 任务（幂等，重复调用不重复起）。
- [ ] **Step 4: 跑测试确认通过**

---

### Task 7: `manager.py` SDK 生命周期与导出钩子

**Files:** Create `app/services/ai/observability/manager.py`; Test `tests/services/ai/observability/test_manager.py`

- [ ] **Step 1: 写失败测试**（`no_infrastructure`；用假 `langfuse` 模块注入 `sys.modules`，不依赖真实 SDK 与网络）

```python
import sys, types
import pytest
from app.services.ai.observability import manager, settings as obs_settings

pytestmark = pytest.mark.no_infrastructure

CONTENT_ATTRS = ("gen_ai.input.messages", "gen_ai.output.messages")


@pytest.fixture(autouse=True)
def _reset():
    manager.reset_for_tests()
    obs_settings.reset_for_tests()
    yield
    manager.reset_for_tests()
    obs_settings.reset_for_tests()


def _install_fake_langfuse(monkeypatch, captured):
    module = types.ModuleType("langfuse")

    class FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def shutdown(self):
            captured["shutdown"] = captured.get("shutdown", 0) + 1

        def flush(self):
            captured["flush"] = captured.get("flush", 0) + 1

    module.Langfuse = FakeClient
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return module


async def test_init_skipped_when_disabled(monkeypatch):
    captured = {}
    _install_fake_langfuse(monkeypatch, captured)
    await manager.init_observability()
    assert captured == {}
    assert manager.current_client() is None


async def test_init_creates_client_with_hooks(monkeypatch):
    captured = {}
    _install_fake_langfuse(monkeypatch, captured)
    await _set_snapshot(enabled=True)
    await manager.init_observability()
    assert captured["init"]["host"] == "http://lf:3000"
    assert captured["init"]["public_key"] == "pk"
    assert callable(captured["init"]["should_export_span"])
    assert callable(captured["init"]["mask_otel_spans"])


async def test_init_failure_degrades_without_raising(monkeypatch):
    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("sdk exploded")

    module = types.ModuleType("langfuse")
    module.Langfuse = Boom
    monkeypatch.setitem(sys.modules, "langfuse", module)
    await _set_snapshot(enabled=True)
    await manager.init_observability()  # 不抛
    assert manager.current_client() is None


async def test_should_export_span_filters_scope_and_sampling(monkeypatch):
    captured = {}
    _install_fake_langfuse(monkeypatch, captured)
    await _set_snapshot(enabled=True, sample_rate=1.0)
    await manager.init_observability()
    predicate = captured["init"]["should_export_span"]
    assert predicate(_fake_span(scope="agentscope")) is True
    assert predicate(_fake_span(scope="nanzi.llm")) is True
    assert predicate(_fake_span(scope="some.random.lib")) is False


async def test_should_export_span_drops_everything_when_disabled(monkeypatch):
    captured = {}
    _install_fake_langfuse(monkeypatch, captured)
    await _set_snapshot(enabled=True)
    await manager.init_observability()
    predicate = captured["init"]["should_export_span"]
    await _set_snapshot(enabled=False)
    assert predicate(_fake_span(scope="agentscope")) is False


async def test_mask_deletes_content_attributes_only_when_capture_off(monkeypatch):
    captured = {}
    _install_fake_langfuse(monkeypatch, captured)
    await _set_snapshot(enabled=True, capture_content=True)
    await manager.init_observability()
    mask = captured["init"]["mask_otel_spans"]
    assert mask(params=_params_with_content()) is None

    await _set_snapshot(enabled=True, capture_content=False)
    result = mask(params=_params_with_content())
    patch = result.span_patches["s1"]
    assert set(patch.delete_attributes) == set(CONTENT_ATTRS)


async def test_shutdown_flushes_and_clears_client(monkeypatch):
    captured = {}
    _install_fake_langfuse(monkeypatch, captured)
    await _set_snapshot(enabled=True)
    await manager.init_observability()
    await manager.shutdown_observability()
    assert captured["flush"] == 1
    assert captured["shutdown"] == 1
    assert manager.current_client() is None
```

配套测试辅助（同文件内定义）：`_set_snapshot(**overrides)` 先 `monkeypatch` 掉 `config_store.load_config` 再 `await obs_settings.refresh_snapshot()`；`_fake_span(scope)` 与 `_params_with_content()` 用 `types.SimpleNamespace` 构造（`attributes` 为 dict、`get_span_context().trace_id` 给固定 int）。

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — `init_observability()`：读快照 → 未启用/未配置则记 warning 并返回 → `from langfuse import Langfuse`（延迟导入）→ 构造带 `host/public_key/secret_key/environment/release/timeout/should_export_span/mask_otel_spans` 的客户端 → 失败则捕获并置 `degraded`。`should_export_span(span)`：快照 enabled → scope 在白名单（`{"agentscope", "nanzi.llm"}`）或含 `gen_ai.` 前缀属性 → `gate.is_sampled(span 的 trace_id 十六进制, sample_rate)`。`mask_otel_spans(*, params)`：`capture_content` 为真返回 `None`，否则为每个含内容属性的 span 构造 `langfuse.types.OtelSpanPatch(delete_attributes=...)` 并返回 `MaskOtelSpansResult`。`shutdown_observability()`：`flush()` + `shutdown()`，异常吞掉。`test_connection()`：用当前快照构造临时客户端并 `flush()` 一次，返回 `(ok, message)`，异常时返回可读原因。`current_client()` / `reset_for_tests()`。
- [ ] **Step 4: 跑测试确认通过**

---

### Task 8: 属性增强中间件与 Agent 链路接线

**Files:** Create `app/services/ai/observability/context_middleware.py`; Modify `app/services/ai/runtime/agentscope/agent_runtime.py`; Test `tests/services/ai/observability/test_context_middleware.py`、`tests/services/ai/test_runtime_middlewares_langfuse.py`

- [ ] **Step 1: 写失败测试**

```python
# test_context_middleware.py
import pytest
from unittest.mock import MagicMock
from app.services.ai.observability.context_middleware import LangfuseContextMiddleware

pytestmark = pytest.mark.no_infrastructure


async def test_middleware_stamps_identity_on_current_span(monkeypatch):
    fake_span = MagicMock()
    monkeypatch.setattr(
        "app.services.ai.observability.context_middleware.otel_trace.get_current_span",
        lambda: fake_span,
    )

    async def next_handler(**_kwargs):
        yield "chunk"

    mw = LangfuseContextMiddleware(user_id="u1", conversation_id="c1",
                                   agent_name="助手", trace_id="t1")
    items = [item async for item in mw.on_reply(agent=MagicMock(), input_kwargs={},
                                                next_handler=next_handler)]
    assert items == ["chunk"]
    set_calls = dict(fake_span.set_attribute.call_args_list and
                     [(c.args[0], c.args[1]) for c in fake_span.set_attribute.call_args_list])
    assert set_calls["user.id"] == "u1"
    assert set_calls["session.id"] == "c1"
    assert set_calls["langfuse.trace.metadata.trace_id"] == "t1"


async def test_middleware_swallows_attribute_errors(monkeypatch):
    class Boom:
        def set_attribute(self, *_a, **_k):
            raise RuntimeError("otel down")

    monkeypatch.setattr(
        "app.services.ai.observability.context_middleware.otel_trace.get_current_span",
        lambda: Boom(),
    )

    async def next_handler(**_kwargs):
        yield "ok"

    mw = LangfuseContextMiddleware(user_id="u1", conversation_id="c1",
                                   agent_name=None, trace_id="t1")
    assert [i async for i in mw.on_reply(agent=MagicMock(), input_kwargs={},
                                         next_handler=next_handler)] == ["ok"]
```

```python
# test_runtime_middlewares_langfuse.py
import pytest
from app.services.ai.runtime.agentscope import agent_runtime

pytestmark = pytest.mark.no_infrastructure


def test_langfuse_middlewares_are_appended_after_stats(monkeypatch):
    monkeypatch.setattr(agent_runtime, "get_current_agent_context", lambda: None)
    mws = agent_runtime.build_runtime_middlewares(
        user_id="u1", conversation_id="c1", agent_name="助手", trace_id="t1")
    names = [type(m).__name__ for m in mws]
    assert names[-2:] == ["TracingMiddleware", "LangfuseContextMiddleware"]
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** `LangfuseContextMiddleware`：`on_reply` 为 async generator 包装；进入时 `otel_trace.get_current_span()` 后逐一 `set_attribute`（全部包在 try/except 内），属性来自 `span_attributes.trace_attributes(...)`；原样透传所有事件。`agent_runtime.build_runtime_middlewares` 末尾追加 `TracingMiddleware()` 与 `LangfuseContextMiddleware(...)`，并在 `middlewares` 构造前后加上「追踪关闭时也不影响」的说明注释。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5:** 跑既有中间件相关测试防回归：`PYTHONPATH=. .venv/bin/python -m pytest tests/ai -q -k middleware`

---

### Task 9: 轮次根 span

**Files:** Create `app/services/ai/observability/turn_span.py`; Modify `app/services/ai/pipeline/runner.py`; Test `tests/services/ai/observability/test_turn_span.py`、`tests/ai/test_pipeline_runner_turn_span.py`

- [ ] **Step 1: 写失败测试**

```python
# test_turn_span.py
import pytest
from types import SimpleNamespace
from app.services.ai.observability import turn_span

pytestmark = pytest.mark.no_infrastructure


def test_turn_span_disabled_when_snapshot_disabled(monkeypatch):
    monkeypatch.setattr(turn_span, "is_tracing_enabled", lambda: False)
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1",
                                     agent_name="助手", trace_id="t1")
    assert span.active is False
    span.end()  # 不抛异常


def test_turn_span_ends_and_detaches(monkeypatch):
    calls = {}
    fake = SimpleNamespace(set_attribute=lambda *a: None, end=lambda: calls.setdefault("ended", True))
    monkeypatch.setattr(turn_span, "is_tracing_enabled", lambda: True)
    monkeypatch.setattr(turn_span, "_start_span", lambda **kw: fake)
    monkeypatch.setattr(turn_span.otel_context, "attach", lambda ctx: "token")
    monkeypatch.setattr(turn_span.otel_context, "detach", lambda token: calls.setdefault("detached", token))
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1",
                                     agent_name="助手", trace_id="t1")
    span.end()
    assert calls["ended"] is True
    assert calls["detached"] == "token"


def test_turn_span_end_is_idempotent(monkeypatch):
    fake = SimpleNamespace(set_attribute=lambda *a: None, end=lambda: None)
    monkeypatch.setattr(turn_span, "is_tracing_enabled", lambda: True)
    monkeypatch.setattr(turn_span, "_start_span", lambda **kw: fake)
    monkeypatch.setattr(turn_span.otel_context, "attach", lambda ctx: "token")
    monkeypatch.setattr(turn_span.otel_context, "detach", lambda token: None)
    span = turn_span.start_turn_span(user_id="u1", conversation_id="c1",
                                     agent_name="助手", trace_id="t1")
    span.end()
    span.end()
```

```python
# tests/ai/test_pipeline_runner_turn_span.py  （验证 run/_run_steps 结构未被破坏）
import inspect
from app.services.ai.pipeline import runner


def test_run_delegates_to_run_steps():
    source = inspect.getsource(runner.PipelineRunner)
    assert "async def _run_steps" in source
    assert "_run_steps(" in source
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** `turn_span.py`：`is_tracing_enabled()`（读 `settings.get_snapshot()` 且 `manager.current_client()` 非空）；`start_turn_span(...)` 在采样通过时创建 span、写入 `span_attributes.trace_attributes(...)`、`otel_context.attach` 并返回可 `end()` 的对象（`end()` 幂等，detach 失败按 AgentScope 的方式吞掉 `ValueError`）；`_start_span` 为可替换的内部函数（`otel_trace.get_tracer("nanzi.llm").start_span(...)`），便于测试注入。
- [ ] **Step 4:** 改造 `runner.py`：把现有 `run` 方法体整体改名为 `_run_steps`（仅改签名行，不改缩进），新增薄包装：

```python
    async def run(self, context: PipelineContext) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.observability.turn_span import start_turn_span

        user_info = getattr(context, "user_info", None) or {}
        span = start_turn_span(
            user_id=user_info.get("user_id") or user_info.get("id"),
            conversation_id=getattr(context, "conversation_id", None),
            agent_name=getattr(context, "agent_name", None),
            trace_id=getattr(context, "trace_id", None),
            extra={"agent_id": getattr(context, "agent_id", None)},
        )
        try:
            async for chunk in self._run_steps(context):
                yield chunk
        finally:
            span.end()
```

- [ ] **Step 5: 跑测试确认通过**
- [ ] **Step 6:** 回归流水线测试：`PYTHONPATH=. .venv/bin/python -m pytest tests/ai -q -k "pipeline or runner"`

---

### Task 10: 启动接线

**Files:** Modify `app/main.py`; Test `tests/api/test_langfuse_lifespan.py`

- [ ] **Step 1: 写失败测试**

```python
import inspect
from app import main


def test_lifespan_wires_observability():
    source = inspect.getsource(main.lifespan)
    assert "init_observability" in source
    assert "shutdown_observability" in source
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** — lifespan 启动段（`AuditService.start_worker()` 之后）调用 `await init_observability()`；关闭段调用 `await shutdown_observability()`。两处都用函数级 import，避免启动期强依赖 `langfuse`。
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5:** 冒烟：`PYTHONPATH=. .venv/bin/python -c "from app.main import app; print('ok')"`

---

### Task 11: 配置接口

**Files:** Create `app/api/portal/endpoints/langfuse.py`; Modify `app/api/portal/api.py`; Test `tests/api/portal/test_langfuse_config_api.py`

- [ ] **Step 1: 写失败测试** — 覆盖：GET 不返回密钥明文、返回 `has_secret_key`；PUT 校验失败返回 400；PUT 成功后 GET 可见新值且 `updated_by` 正确；`/test` 在配置不完整时返回可读失败原因而非 500；无权限用户访问返回 403（复用 conftest 的 `seed_data` 与既有 API 测试写法）。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现** `langfuse.py`：三个端点分别用 `require_permission("menu", "menu:system:config")` / `require_permission("element", "element:system:langfuse_save")`；PUT 收 `dict` body，调 `config_store.save_config(payload, changed_by=user.get("user_name"))` 后触发 `settings.refresh_snapshot()`；GET 返回配置（去掉 `secret_key`，加 `has_secret_key`、`status`、`last_export`）；`/test` 调 `manager.test_connection()`。`api.py` 注册 `portal_router.include_router(langfuse.router, prefix="/system/langfuse", tags=["Langfuse 可观测性"], dependencies=[Depends(require_api_key)])`，放在「7. 系统配置」之后。
- [ ] **Step 4: 跑测试确认通过**

---

### Task 12: 迁移 SQL 与契约测试

**Files:** Create `db-prod/V161-create-langfuse-config.sql`, `db-prod-pg/V62-create-langfuse-config.sql`, `tests/test_langfuse_migration_contract.py`

- [ ] **Step 1: 写失败契约测试** — 断言两个文件都存在；都含 `CREATE TABLE IF NOT EXISTS` + `langfuse_config` + 全部列名；都插入 `id` 为 1 的默认行且 `enabled` 为假值；都插入 `element:system:langfuse_save`；PG 侧含 `BOOLEAN` / `FALSE` / `NUMERIC` 且不含 `TINYINT`、`DATETIME`。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 写两个 SQL**（头部注释按 `db-prod/V81-add_span_columns_to_traces.sql:1-7` 的规范，含「不由 Agent 自动执行」说明）：

MySQL：
```sql
CREATE TABLE IF NOT EXISTS `langfuse_config` (
  `id` INT NOT NULL DEFAULT 1,
  `enabled` TINYINT(1) NOT NULL DEFAULT 0,
  `host` VARCHAR(512) NULL,
  `public_key` VARCHAR(255) NULL,
  `secret_key` TEXT NULL,
  `sample_rate` DECIMAL(4,3) NOT NULL DEFAULT 1.000,
  `capture_content` TINYINT(1) NOT NULL DEFAULT 1,
  `environment` VARCHAR(64) NULL,
  `release` VARCHAR(64) NULL,
  `timeout_seconds` INT NOT NULL DEFAULT 5,
  `trace_url_template` VARCHAR(512) NULL,
  `updated_by` VARCHAR(64) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Langfuse LLM 链路追踪配置（单行）';

INSERT IGNORE INTO `langfuse_config`
  (`id`, `enabled`, `sample_rate`, `capture_content`, `timeout_seconds`)
VALUES (1, 0, 1.000, 1, 5);

INSERT IGNORE INTO `ai_agent_resource_permissions`
  (`resource_type`, `resource_id`, `enabled`, `created_at`, `updated_at`)
VALUES ('element', 'element:system:langfuse_save', 1, NOW(), NOW());
```
PG 版同构，列类型换成 `INTEGER / BOOLEAN / VARCHAR / TEXT / NUMERIC(4,3) / TIMESTAMP`，默认行用 `INSERT ... ON CONFLICT ("id") DO NOTHING`，权限行同理。

- [ ] **Step 4: 跑契约测试确认通过**
- [ ] **Step 5: 回归迁移脚本测试** — `PYTHONPATH=. .venv/bin/python -m pytest tests/test_db_prod_apply_sql.py tests/test_db_prod_pg_apply_sql.py -q`

---

### Task 13: 前端独立 Tab

**Files:** Create `frontend/src/components/system/LangfuseConfig.vue`; Modify `frontend/src/views/SystemConfig.vue`, `frontend/src/constants/permissions.ts`; Test `tests/frontend/test_langfuse_config_contract.py`

- [ ] **Step 1: 写失败契约测试** — 断言 `SystemConfig.vue`：`activeTab` 联合类型含 `'langfuse'`、Tab 按钮文本含 `Langfuse`、面板 `v-else-if="activeTab === 'langfuse'"` 存在且渲染 `LangfuseConfig`、import 语句存在；断言 `frontend/src/components/system/LangfuseConfig.vue` 存在且包含 `element:system:langfuse_save`、`/api/portal/system/langfuse`、`测试连接` 字样；断言 `permissions.ts` 含 `element:system:langfuse_save`。
- [ ] **Step 2: 跑测试确认失败** — `PYTHONPATH=. .venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_langfuse_config_contract.py -q`
- [ ] **Step 3: 实现组件** `LangfuseConfig.vue`（照 `ModelRegistry.vue` 的加载/保存/Toast 模式）：`onMounted` 拉 `GET /api/portal/system/langfuse`；表单字段与表一一对应；密钥输入框 placeholder 显示「已配置，留空表示不修改」并只在新输入时提交；保存用 `PUT`，按钮按 `useUser().hasPermission('element:system:langfuse_save')` 控制禁用；状态卡片展示 `status` / `last_export` / 是否已注册 Provider；「测试连接」调 `POST .../test` 并展示返回原因。
- [ ] **Step 4: 实现 Tab** — `SystemConfig.vue` 三处：`activeTab` 联合类型加 `'langfuse'`（:53）、Tab 按钮（:3032 之后，`ChartBarSquareIcon`）、内容面板 `v-else-if="activeTab === 'langfuse'"` 渲染 `<LangfuseConfig />`；`permissions.ts` 加 `{ id: 'element:system:langfuse_save', label: '保存 Langfuse 配置' }`。
- [ ] **Step 5: 跑契约测试确认通过**
- [ ] **Step 6: 类型检查** — `cd frontend && npx vue-tsc --noEmit -p tsconfig.app.json` → 无错误

---

### Task 14: 文档与收尾

**Files:** Modify `tests/CHECKLIST.md`

- [ ] **Step 1:** 在 `tests/CHECKLIST.md` 登记新增测试文件与运行命令（后端单测、接口测试、迁移契约、前端契约、类型检查）。
- [ ] **Step 2: 全量回归** — `PYTHONPATH=. .venv/bin/python -m pytest tests/ -q`（若历史用例存在与本改动无关的既有失败，逐一确认并记录，不掩盖）
- [ ] **Step 3:** 输出交付清单：改动文件、验证结果、待用户执行的迁移 SQL 与 `./dev.sh` 提醒。

---

## 自检

- **规格覆盖**：模块结构（Task 2-7）、Agent 链路（Task 8）、轮次根 span（Task 9）、启动接线（Task 10）、配置独立表与接口（Task 4/11）、独立 Tab（Task 13）、迁移（Task 12）、测试与 CHECKLIST（Task 14）——均有对应任务；「直调出口埋点」与「跳转入口」按规格属二期，本计划不含。
- **占位符**：无 TBD/TODO；每个实现步骤给出了接口签名与关键行为，测试步骤给出可执行断言。
- **命名一致性**：`load_config` / `save_config` / `load_raw_row` / `get_snapshot` / `refresh_snapshot` / `init_observability` / `shutdown_observability` / `current_client` / `test_connection` / `is_sampled` / `trace_attributes` / `generation_attributes` / `extract_usage` / `start_turn_span` / `LangfuseContextMiddleware` 在各任务间保持一致。
