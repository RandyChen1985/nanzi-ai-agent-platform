# API 工具请求签名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 API 工具增加可开关的 HMAC-SHA256 请求签名、密钥加密存储、试跑快照，以及编辑页开关和业务方接入说明弹层。

**Architecture:** 签名逻辑集中在 `request_signing.py`，由 `GenericApiToolFactory._perform_request` 在最终请求构造完成后调用；签名配置存 `sys_api_tools` 新增字段，密钥 Fernet 加密、只写不回显。前端复用一份签名表单组件，编辑页和向导共用；试跑把草稿签名配置交给同一后端内核。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、SQLAlchemy async、httpx、Vue 3 + TypeScript + Tailwind、pytest、Node `--experimental-strip-types`。

**设计依据:** `docs/superpowers/specs/2026-10-19-api-tool-request-signing-design.md`

---

## File Structure

**Backend**
- Create: `app/services/ai/tools/request_signing.py` — canonical string、HMAC、密钥加解密、配置校验。
- Modify: `app/models/tool.py` — 四个签名字段和 `signing_secret_configured` 属性。
- Modify: `app/schemas/tool.py` — CRUD/试跑 schema，密钥只写。
- Modify: `app/api/portal/endpoints/tools.py` — 创建/更新校验、试跑密钥解析、错误分类。
- Modify: `app/services/ai/tools/generic_api.py` — 最终请求构造后签名。
- Create: `db-prod/V173-add-api-tool-request-signing.sql`
- Create: `db-prod-pg/V74-add-api-tool-request-signing.sql`

**Frontend**
- Create: `frontend/src/utils/requestSigning.ts` — 密钥生成、格式校验、帮助文档常量。
- Create: `frontend/src/components/system/ToolSigningEditor.vue` — 开关、密钥、轮换、帮助入口。
- Create: `frontend/src/components/system/RequestSigningHelpModal.vue` — 接入说明弹层。
- Modify: `frontend/src/api/tool.ts` — 类型和预览载荷。
- Modify: `frontend/src/components/system/ToolRegistry.vue` — 表单状态、保存、试跑参数。
- Modify: `frontend/src/components/system/ToolWizard.vue` — 共用签名表单和试跑参数。
- Modify: `frontend/src/components/system/ApiToolTester.vue` — 预览携带签名配置、显示签名 Header。

**Tests**
- Create: `tests/ai/tools/test_request_signing.py`
- Create: `tests/api/portal/test_tool_request_signing.py`
- Create: `tests/frontend/test_api_tool_request_signing_contract.py`
- Update: `tests/CHECKLIST.md`

---

### Task 1: 签名核心模块

**Files:**
- Create: `app/services/ai/tools/request_signing.py`
- Test: `tests/ai/tools/test_request_signing.py`

- [ ] **Step 1: 写固定测试向量和规范化测试**

```python
import hashlib
import hmac

from app.services.ai.tools.request_signing import (
    canonicalize_query,
    build_canonical_request,
    sign_request,
)

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
KEY_ID = "8f3c2a1b7d9e4c60"
TIMESTAMP = "1729334695"
NONCE = "5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f"


def test_fixed_vector_matches_spec():
    headers = sign_request(
        method="POST",
        raw_path="/api/orders",
        raw_query="a=1&b=2",
        body=b'{"amount":100,"applicant":"alice"}',
        secret=SECRET,
        key_id=KEY_ID,
        timestamp=TIMESTAMP,
        nonce=NONCE,
    )
    assert headers["X-Nanzi-Signature-Version"] == "1"
    assert headers["X-Nanzi-Timestamp"] == TIMESTAMP
    assert headers["X-Nanzi-Nonce"] == NONCE
    assert headers["X-Nanzi-Key-Id"] == KEY_ID
    assert headers["X-Nanzi-Signature"] == (
        "9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad"
    )


def test_canonical_query_sorts_pairs_and_uses_rfc3986():
    assert canonicalize_query("b=2&a=hello+world&a=1") == "a=1&a=hello%20world&b=2"
    assert canonicalize_query("") == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/ai/tools/test_request_signing.py -q
```

Expected: `ModuleNotFoundError` 或 `ImportError`，因为模块还不存在。

- [ ] **Step 3: 实现模块**

```python
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote

from app.utils.encryption import get_api_key_manager

SIGNATURE_VERSION = "1"
SIGNATURE_ALGORITHM = "hmac-sha256"
SIGNATURE_PREFIX = "X-Nanzi-"
SIGNATURE_HEADERS = (
    "X-Nanzi-Signature-Version",
    "X-Nanzi-Key-Id",
    "X-Nanzi-Timestamp",
    "X-Nanzi-Nonce",
    "X-Nanzi-Signature",
)
SECRET_PREFIX = "sig:v1:"
_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SECRET_RE = re.compile(r"^[\x21-\x7e]{32,128}$")


class SigningConfigError(ValueError):
    """签名配置不合法；调用方必须失败关闭，不能退化成未签名请求。"""


@dataclass(frozen=True)
class SigningConfig:
    enabled: bool
    algorithm: str
    key_id: str | None
    secret: str | None


def validate_signing_key_id(key_id: str | None) -> str:
    value = str(key_id or "").strip()
    if not _KEY_ID_RE.fullmatch(value):
        raise SigningConfigError("签名 Key-Id 必须是 16 位小写十六进制")
    return value


def validate_signing_secret(secret: str | None) -> str:
    value = str(secret or "")
    if not _SECRET_RE.fullmatch(value):
        raise SigningConfigError("签名密钥必须是 32-128 个可打印 ASCII 字符")
    return value


def encrypt_signing_secret(secret: str) -> str:
    return f"{SECRET_PREFIX}{get_api_key_manager().encrypt_api_key(validate_signing_secret(secret))}"


def decrypt_signing_secret(stored: str) -> str:
    value = str(stored or "")
    if not value.startswith(SECRET_PREFIX):
        raise SigningConfigError("签名密钥存储格式不合法")
    try:
        return get_api_key_manager().decrypt_api_key(value[len(SECRET_PREFIX):])
    except Exception as exc:
        raise SigningConfigError("签名密钥无法解密") from exc


def parse_signing_config(tool: Any) -> SigningConfig:
    enabled = bool(getattr(tool, "signing_enabled", False))
    algorithm = str(getattr(tool, "signing_algorithm", "") or SIGNATURE_ALGORITHM).lower()
    key_id = str(getattr(tool, "signing_key_id", "") or "").strip()
    stored_secret = str(getattr(tool, "signing_secret_encrypted", "") or "").strip()
    if not enabled:
        return SigningConfig(False, algorithm, key_id or None, None)
    if algorithm != SIGNATURE_ALGORITHM:
        raise SigningConfigError(f"不支持的签名算法：{algorithm}")
    validate_signing_key_id(key_id)
    if not stored_secret:
        raise SigningConfigError("已开启请求签名，但没有配置签名密钥")
    secret = validate_signing_secret(decrypt_signing_secret(stored_secret))
    return SigningConfig(True, algorithm, key_id, secret)


def validate_no_reserved_signing_headers(headers: Mapping[str, Any] | None) -> None:
    for key in headers or {}:
        if str(key).lower().startswith(SIGNATURE_PREFIX.lower()):
            raise SigningConfigError(f"普通 Headers 不能使用平台保留前缀：{key}")


def canonicalize_query(raw_query: str) -> str:
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    pairs.sort(key=lambda pair: (pair[0].encode("utf-8"), pair[1].encode("utf-8")))
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}"
        for key, value in pairs
    )


def build_canonical_request(
    *,
    method: str,
    raw_path: str,
    raw_query: str,
    body: bytes,
    timestamp: str,
    nonce: str,
    key_id: str,
    version: str = SIGNATURE_VERSION,
) -> str:
    return "\n".join([
        version,
        method.upper(),
        raw_path,
        canonicalize_query(raw_query),
        hashlib.sha256(body).hexdigest(),
        str(timestamp),
        nonce,
        key_id,
    ])


def sign_request(
    *,
    method: str,
    raw_path: str,
    raw_query: str,
    body: bytes,
    secret: str,
    key_id: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    nonce_value = nonce or secrets.token_hex(16)
    key_id_value = validate_signing_key_id(key_id)
    canonical = build_canonical_request(
        method=method,
        raw_path=raw_path,
        raw_query=raw_query,
        body=body,
        timestamp=timestamp_value,
        nonce=nonce_value,
        key_id=key_id_value,
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Nanzi-Signature-Version": SIGNATURE_VERSION,
        "X-Nanzi-Key-Id": key_id_value,
        "X-Nanzi-Timestamp": timestamp_value,
        "X-Nanzi-Nonce": nonce_value,
        "X-Nanzi-Signature": signature,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
.venv/bin/python -m pytest tests/ai/tools/test_request_signing.py -q
```

Expected: PASS。

---

### Task 2: 模型、迁移、Schema 与 CRUD 校验

**Files:**
- Modify: `app/models/tool.py`
- Modify: `app/schemas/tool.py`
- Modify: `app/api/portal/endpoints/tools.py`
- Create: `db-prod/V173-add-api-tool-request-signing.sql`
- Create: `db-prod-pg/V74-add-api-tool-request-signing.sql`
- Test: `tests/api/portal/test_tool_request_signing.py`

- [ ] **Step 1: 写 API 测试**

覆盖：

```python
async def test_create_rejects_signing_without_secret(client, admin_api_key):
    response = await client.post(
        "/api/portal/tools",
        json={
            "name": f"signing-missing-{uuid.uuid4().hex[:8]}",
            "method": "POST",
            "url_template": "https://api.test/refund",
            "signing_enabled": True,
        },
        headers={"X-API-Key": admin_api_key},
    )
    assert response.status_code == 400


async def test_create_never_returns_secret(client, admin_api_key):
    secret = "a" * 64
    response = await client.post(
        "/api/portal/tools",
        json={
            "name": f"signing-{uuid.uuid4().hex[:8]}",
            "method": "POST",
            "url_template": "https://api.test/refund",
            "signing_enabled": True,
            "signing_key_id": "0123456789abcdef",
            "signing_secret": secret,
        },
        headers={"X-API-Key": admin_api_key},
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["signing_secret_configured"] is True
    assert "signing_secret" not in body
    assert secret not in response.text
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest tests/api/portal/test_tool_request_signing.py -q
```

- [ ] **Step 3: 添加模型字段和属性**

```python
signing_enabled = Column(Boolean, default=False, nullable=False)
signing_key_id = Column(String(64), nullable=True)
signing_secret_encrypted = Column(Text, nullable=True)
signing_algorithm = Column(String(32), nullable=False, default="hmac-sha256")

@property
def signing_secret_configured(self) -> bool:
    return bool(self.signing_secret_encrypted)
```

- [ ] **Step 4: 添加迁移**

MySQL `db-prod/V173-add-api-tool-request-signing.sql`：

```sql
ALTER TABLE `sys_api_tools`
  ADD COLUMN `signing_enabled` TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN `signing_key_id` VARCHAR(64) NULL,
  ADD COLUMN `signing_secret_encrypted` TEXT NULL,
  ADD COLUMN `signing_algorithm` VARCHAR(32) NOT NULL DEFAULT 'hmac-sha256';
CREATE UNIQUE INDEX `uk_sys_api_tools_signing_key_id`
  ON `sys_api_tools` (`signing_key_id`);
```

PostgreSQL `db-prod-pg/V74-add-api-tool-request-signing.sql`：

```sql
ALTER TABLE sys_api_tools
  ADD COLUMN signing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN signing_key_id VARCHAR(64) NULL,
  ADD COLUMN signing_secret_encrypted TEXT NULL,
  ADD COLUMN signing_algorithm VARCHAR(32) NOT NULL DEFAULT 'hmac-sha256';
CREATE UNIQUE INDEX uk_sys_api_tools_signing_key_id
  ON sys_api_tools (signing_key_id);
```

- [ ] **Step 5: 扩展 Schema**

`SysApiToolBase` 增加：

```python
signing_enabled: bool = False
signing_key_id: Optional[str] = None
signing_algorithm: str = "hmac-sha256"
```

`SysApiToolCreate` 增加 `signing_secret: Optional[str] = None`。

`SysApiToolUpdate` 增加 `signing_enabled`、`signing_key_id`、`signing_algorithm`、`signing_secret`。

`SysApiToolResponse` 增加 `signing_secret_configured: bool = False`。

`SysApiToolPreviewRequest` 增加：

```python
signing_enabled: bool = False
signing_algorithm: str = "hmac-sha256"
signing_key_id: Optional[str] = None
signing_secret: Optional[str] = None
tool_id: Optional[str] = None
```

- [ ] **Step 6: CRUD 校验与密钥加密**

在 `tools.py` 增加 helper：

```python
def _prepare_signing_for_create(payload: SysApiToolCreate) -> tuple[dict, str | None]:
    data = payload.model_dump()
    secret = data.pop("signing_secret", None)
    if data.get("signing_enabled"):
        key_id = validate_signing_key_id(data.get("signing_key_id"))
        secret = validate_signing_secret(secret)
        data["signing_key_id"] = key_id
        data["signing_algorithm"] = "hmac-sha256"
        data["signing_secret_encrypted"] = encrypt_signing_secret(secret)
    elif secret or data.get("signing_key_id"):
        raise SigningConfigError("未开启请求签名时不能提交签名密钥")
    return data, secret
```

更新时：

```python
update_data = tool_in.model_dump(exclude_unset=True)
secret = update_data.pop("signing_secret", None)
if secret or "signing_key_id" in update_data:
    if not secret or not update_data.get("signing_key_id"):
        raise SigningConfigError("轮换签名密钥必须同时提供 Key-Id")
    validate_signing_key_id(update_data["signing_key_id"])
    update_data["signing_secret_encrypted"] = encrypt_signing_secret(secret)
if update_data.get("signing_enabled") and not (
    update_data.get("signing_secret_encrypted") or tool.signing_secret_encrypted
):
    raise SigningConfigError("已开启请求签名，但没有配置签名密钥")
```

同时校验普通 Headers 不能以 `x-nanzi-` 开头。

- [ ] **Step 7: 运行测试**

```bash
.venv/bin/python -m pytest tests/api/portal/test_tool_request_signing.py tests/api/portal/test_tool_system_params.py tests/api/portal/test_tool_preview.py -q
```

Expected: PASS。

---

### Task 3: 请求内核签名与试跑

**Files:**
- Modify: `app/services/ai/tools/generic_api.py`
- Modify: `app/api/portal/endpoints/tools.py`
- Test: `tests/ai/tools/test_request_signing.py`
- Test: `tests/api/portal/test_tool_request_signing.py`

- [ ] **Step 1: 添加内核签名测试**

```python
@pytest.mark.asyncio
async def test_perform_request_adds_signature_headers():
    config = SysApiTool(
        name="signed",
        method="POST",
        url_template="https://api.test/refund",
        parameter_schema={"properties": {"amount": {"type": "number"}}},
        signing_enabled=True,
        signing_key_id="0123456789abcdef",
        signing_algorithm="hmac-sha256",
        signing_secret_encrypted=encrypt_signing_secret(SECRET),
    )
    capture = {}
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        request.return_value = httpx.Response(200, json={"ok": True})
        await GenericApiToolFactory._perform_request(
            config, {"amount": 100}, capture=capture,
        )
        _args, kwargs = request.call_args

    headers = capture["headers"]
    assert headers["X-Nanzi-Signature-Version"] == "1"
    assert kwargs["headers"]["X-Nanzi-Signature"] == headers["X-Nanzi-Signature"]
```

- [ ] **Step 2: 实现签名分支**

在 `_perform_request` 中保留未签名路径完全不动；签名开启时：

```python
signing = parse_signing_config(config)
if signing.enabled:
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        request = client.build_request(
            method,
            url,
            params=remaining_params if method == "GET" else None,
            json=remaining_params if method != "GET" else None,
            headers=headers,
        )
        raw_path = request.url.raw_path.split(b"?", 1)[0].decode("ascii")
        raw_query = request.url.query.decode("ascii")
        signature_headers = sign_request(
            method=method,
            raw_path=raw_path,
            raw_query=raw_query,
            body=request.content,
            secret=signing.secret or "",
            key_id=signing.key_id or "",
        )
        request.headers.update(signature_headers)
        headers = dict(request.headers)
        if capture is not None:
            capture.update({
                "method": method,
                "url": str(request.url),
                "headers": dict(headers),
                "query": dict(remaining_params) if method == "GET" else {},
                "body": remaining_params if method != "GET" else None,
            })
        if method == "GET":
            response = await client.get(str(request.url), headers=headers)
        else:
            response = await client.request(
                method, str(request.url), content=request.content, headers=headers,
            )
```

- [ ] **Step 3: 试跑解析密钥**

`preview_tool` 增加 `db` 依赖；按优先级取 `payload.signing_secret`，否则按 `payload.tool_id` 读取已保存工具并解密。构造 `draft` 时写入 `signing_enabled`、`signing_key_id`、`signing_algorithm`、`signing_secret_encrypted`。

新增异常处理：

```python
except SigningConfigError as exc:
    return SysApiToolPreviewResponse(
        ok=False,
        error_kind="invalid_signing_config",
        message=str(exc),
        request=request_info(),
    )
```

- [ ] **Step 4: 运行内核与试跑测试**

```bash
.venv/bin/python -m pytest tests/ai/tools/test_request_signing.py tests/api/portal/test_tool_request_signing.py tests/ai/tools/test_generic_api.py tests/ai/tools/test_system_params.py -q
```

Expected: PASS；未签名路径测试保持通过。

---

### Task 4: 前端 API、生成工具和签名编辑组件

**Files:**
- Modify: `frontend/src/api/tool.ts`
- Create: `frontend/src/utils/requestSigning.ts`
- Create: `frontend/src/components/system/RequestSigningHelpModal.vue`
- Create: `frontend/src/components/system/ToolSigningEditor.vue`
- Test: `tests/frontend/test_api_tool_request_signing_contract.py`

- [ ] **Step 1: 加契约测试**

断言源码包含：

```python
def test_signing_editor_has_switch_secret_and_help_entry():
    source = EDITOR.read_text()
    assert "请求签名" in source
    assert "签名密钥" in source
    assert "查看请求签名接入说明" in source
    assert "RequestSigningHelpModal" in source


def test_help_modal_documents_verification_and_test_vector():
    source = HELP.read_text()
    for text in (
        "X-Nanzi-Timestamp",
        "X-Nanzi-Nonce",
        "X-Nanzi-Signature",
        "canonical_query",
        "9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad",
    ):
        assert text in source
    assert "固定测试向量" in source
    assert "当前真实密钥" not in source
```

- [ ] **Step 2: 扩展 API 类型**

`SysApiTool` / Create / Update 增加：

```ts
signing_enabled?: boolean
signing_key_id?: string | null
signing_algorithm?: string
signing_secret?: string
signing_secret_configured?: boolean
```

`SysApiToolPreviewPayload` 增加：

```ts
signing_enabled?: boolean
signing_algorithm?: string
signing_key_id?: string | null
signing_secret?: string
tool_id?: string | null
```

`PreviewErrorKind` 增加 `'invalid_signing_config'`。

- [ ] **Step 3: 写生成与校验工具**

```ts
export const SIGNING_ALGORITHM = 'hmac-sha256'

export function generateSigningKeyId(): string {
  const bytes = new Uint8Array(8)
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error('当前浏览器不支持安全随机数，无法生成签名密钥')
  }
  globalThis.crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function generateSigningSecret(): string {
  const bytes = new Uint8Array(32)
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error('当前浏览器不支持安全随机数，无法生成签名密钥')
  }
  globalThis.crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}
```

- [ ] **Step 4: 写帮助弹层**

使用 `Modal.vue`、`CopyButton.vue`，内容固定为设计稿第 3.3/3.7 节：Header、5 步验签、签名串、Python 示例、测试向量、注意事项。所有代码块可复制，不出现真实密钥。

- [ ] **Step 5: 写 `ToolSigningEditor.vue`**

Props 使用 `v-model:enabled`、`v-model:keyId`、`v-model:secret`、`:configured`；开关标题右侧放 `?`，打开 `RequestSigningHelpModal`。生成按钮调用 `generateSigningSecret()` / `generateSigningKeyId()` 并设置 enabled=true。

- [ ] **Step 6: 运行前端契约与类型检查**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_api_tool_request_signing_contract.py -q
npx vue-tsc --noEmit
```

---

### Task 5: 接入表单、向导与试跑抽屉

**Files:**
- Modify: `frontend/src/components/system/ToolRegistry.vue`
- Modify: `frontend/src/components/system/ToolWizard.vue`
- Modify: `frontend/src/components/system/ApiToolTester.vue`
- Test: `tests/frontend/test_api_tool_request_signing_contract.py`

- [ ] **Step 1: 扩展 `toolForm`**

增加：

```ts
signing_enabled: false,
signing_key_id: null,
signing_algorithm: 'hmac-sha256',
signing_secret: '',
signing_secret_configured: false,
```

编辑已有工具时保留 `signing_*`，清空 `signing_secret`；克隆工具时强制 `signing_secret_configured=false`。

- [ ] **Step 2: 表单和向导渲染组件**

`ToolRegistry.vue` 在 Headers 后放：

```vue
<ToolSigningEditor
  v-model:enabled="toolForm.signing_enabled"
  v-model:key-id="toolForm.signing_key_id"
  v-model:secret="toolForm.signing_secret"
  :configured="toolForm.signing_secret_configured"
/>
```

`ToolWizard.vue` 第 4 步同样渲染，并把签名参数传给 `ApiToolTester`。

- [ ] **Step 3: 保存校验与载荷**

开启签名时：已有 `signing_secret_configured` 或本次输入了 `signing_secret` 才允许保存；否则 toast。新建工具必须同时有 `signing_key_id` 和 `signing_secret`。

保存载荷包含：

```ts
signing_enabled: !!toolForm.signing_enabled,
signing_key_id: toolForm.signing_key_id || null,
signing_algorithm: 'hmac-sha256',
...(toolForm.signing_secret ? { signing_secret: toolForm.signing_secret } : {}),
```

保存成功后清空 `signing_secret`。

- [ ] **Step 4: 试跑携带签名配置**

`ApiToolTester` props 增加：

```ts
signingEnabled?: boolean
signingAlgorithm?: string
signingKeyId?: string | null
signingSecret?: string
toolId?: string | null
```

`preview()` 载荷增加这些字段（`toolId` 用 `tool_id`）。抽屉里在请求详情上方显示“请求签名已开启”，并让 `Headers` 快照展示 `X-Nanzi-*`。

- [ ] **Step 5: 运行契约测试与类型检查**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend/test_api_tool_request_signing_contract.py tests/frontend/test_api_tool_tester_drawer_contract.py tests/frontend/test_api_tool_form_preview_contract.py -q
npx vue-tsc --noEmit
```

---

### Task 6: 全量回归与清单

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 运行后端相关回归**

```bash
.venv/bin/python -m pytest tests/ai/tools/test_request_signing.py tests/ai/tools/test_generic_api.py tests/ai/tools/test_system_params.py tests/api/portal/test_tool_request_signing.py tests/api/portal/test_tool_system_params.py tests/api/portal/test_tool_preview.py -q
```

- [ ] **Step 2: 运行前端全量**

```bash
.venv/bin/python -m pytest --confcutdir=tests/frontend tests/frontend -q
npx vue-tsc --noEmit
git diff --check
```

- [ ] **Step 3: 更新 `tests/CHECKLIST.md`**

记录：

- 签名协议/Header/测试向量；
- 新增迁移文件；
- 后端/前端测试结果；
- 密钥只写不回显和失败关闭；
- 未执行 `./dev.sh`、未提交 git（按仓库约定由用户决定）。

---

## 风险与边界

- 未开启签名的工具必须走原有 `get`/`request` 分支，避免破坏已有 mock 测试。
- 签名开启时必须使用构造后的最终 URL/body，不能在 `json=` 序列化前另算一份。
- 密钥解密失败、配置缺失、普通 Header 使用 `X-Nanzi-*` 时失败关闭。
- 试跑已保存工具通过 `tool_id` 取密钥；新建草稿使用表单本次输入的密钥。
- 签名不防止模型乱传业务参数，也不替代 HTTPS；文档和弹层都要写明。


---

## 执行记录（2026-10-19）

- [x] 签名核心、固定向量、查询规范化、失败关闭。
- [x] 模型字段、MySQL `V173` / PostgreSQL `V74` 迁移、CRUD 校验与密钥加密。
- [x] `_perform_request` 对最终 URL/query/raw body 签名，试跑复用同一内核。
- [x] 前端签名编辑组件、帮助弹层、表单/向导共用状态、试跑抽屉 Header 展示。
- [x] 审计脱敏名单补 `signing_secret` / `signingSecret`。
- [x] 后端定向回归 **147 passed**；前端全量 **2337 passed / 1 skipped**。
- [x] `vue-tsc --noEmit -p tsconfig.app.json` 总错误 **81（改动前基线 82）**；本次新增签名文件无新增错误，并顺手修掉 `ToolRegistry` 的既存类型错误。
- [x] 本地 MySQL 已有 `V173` 新列，真实库创建/更新端到端用例通过；PostgreSQL `V74` 未在本机验证。
- [ ] 未执行 `./dev.sh`、未执行 `git commit`（按仓库约定由用户处理）。
