# API 工具「系统级参数」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 API 工具编辑器中增加一组模型不可见、运行时由平台强制注入的系统级参数，支持 `{user.xxx}` 用户变量并按现有规则落到 path / query / body。

**Architecture:** 系统级参数存放在 `parameter_schema` 顶层的 `x-nanzi-system-params` 数组里，不进入 `properties`，因此模型天然看不到。新增独立的后端解析/校验/注入模块，`GenericApiToolFactory._perform_request` 在普通参数替换之后、路径替换之前调用它。前端扩展 `toolSchema.ts` 的解析/序列化，并新增独立的 `ToolSystemParamEditor.vue` 区域。

**Tech Stack:** Python 3.11 / FastAPI / Pydantic 2 / pytest；Vue 3 / TypeScript / Vite 7 / Tailwind CSS 3 / Node `--experimental-strip-types` / `vue-tsc`。

**项目规则:** 未经用户明确要求不执行 `git commit`。每个任务以测试通过为检查点；全部完成后再由用户决定提交方式。

---

## 文件结构

### 新增

- `app/services/ai/tools/system_params.py`：系统级参数的解析、校验、注入；不依赖 FastAPI。
- `frontend/src/components/system/ToolSystemParamEditor.vue`：系统级参数的可视化编辑区域。
- `tests/ai/tools/test_system_params.py`：后端纯逻辑 + 运行时注入测试。
- `tests/api/portal/test_tool_system_params.py`：保存/试跑接口契约测试。
- `tests/frontend/tool_schema_system_params.mjs`：schema 解析/序列化 Node 行为测试。
- `tests/frontend/test_api_tool_system_params_contract.py`：编辑器和试跑区的源码契约测试。

### 修改

- `app/services/ai/tools/generic_api.py`：扁平 schema 过滤 `x-*`；运行时注入系统级参数。
- `app/api/portal/endpoints/tools.py`：保存/试跑校验；试跑路径预检排除系统级参数。
- `app/schemas/tool.py`：试跑错误类型增加 `invalid_parameter_schema`。
- `frontend/src/utils/toolSchema.ts`：`SystemParam` 类型、解析/序列化、校验函数。
- `frontend/src/components/system/ToolParamEditor.vue`：加载/回写 systemParams，嵌入新组件，更新帮助文案。
- `frontend/src/components/system/ApiToolTester.vue`：展示系统级参数只读提示与新的错误类型。
- `frontend/src/components/system/ToolRegistry.vue`：保存前调用系统级参数校验。
- `tests/CHECKLIST.md`：新增交付清单行。

---

### Task 1: 后端系统级参数模块

**Files:**
- Create: `app/services/ai/tools/system_params.py`
- Test: `tests/ai/tools/test_system_params.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/ai/tools/test_system_params.py`：

```python
"""系统级参数：模型不可见，运行时强制注入。"""
from __future__ import annotations

import pytest

from app.services.ai.tools.system_params import (
    SystemParamConfigError,
    apply_system_params,
    parse_system_params,
    validate_system_params,
)
from app.services.ai.tools.user_placeholders import UserPlaceholderError, resolve_user_variables

pytestmark = pytest.mark.no_infrastructure

USER_INFO = {"user_id": "42", "user_name": "alice", "dept_code": "D001"}


def _schema():
    return {
        "properties": {"amount": {"type": "number"}},
        "x-nanzi-system-params": [
            {
                "name": "applicant",
                "type": "string",
                "value": "{user.user_name}",
                "description": "申请人",
            }
        ],
    }


def test_parse_system_params_reads_valid_entries():
    specs = parse_system_params(_schema())

    assert [(s.name, s.type, s.value) for s in specs] == [
        ("applicant", "string", "{user.user_name}")
    ]
    assert specs[0].description == "申请人"


def test_validate_rejects_duplicate_and_collision_with_normal_param():
    duplicate = {
        "x-nanzi-system-params": [
            {"name": "applicant", "value": "{user.user_name}"},
            {"name": "applicant", "value": "{user.real_name}"},
        ]
    }
    with pytest.raises(SystemParamConfigError, match="重复"):
        validate_system_params(duplicate)

    collision = {
        "properties": {"applicant": {"type": "string"}},
        "x-nanzi-system-params": [
            {"name": "applicant", "value": "{user.user_name}"}
        ],
    }
    with pytest.raises(SystemParamConfigError, match="重名"):
        validate_system_params(collision)


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"name": "", "value": "x"}, "缺少名称"),
        ({"name": "1bad", "value": "x"}, "不合法"),
        ({"name": "applicant", "value": ""}, "缺少固定值"),
        ({"name": "applicant", "value": "x", "type": "object"}, "类型不支持"),
    ],
)
def test_validate_rejects_invalid_entries(entry, message):
    with pytest.raises(SystemParamConfigError, match=message):
        validate_system_params({"x-nanzi-system-params": [entry]})


def test_apply_replaces_user_variable_and_overrides_model_value():
    variables = resolve_user_variables(USER_INFO)

    prepared = apply_system_params(
        _schema(),
        {"amount": 100, "applicant": "mallory"},
        variables,
    )

    assert prepared == {"amount": 100, "applicant": "alice"}


def test_apply_raises_for_missing_variable():
    with pytest.raises(UserPlaceholderError):
        apply_system_params(
            _schema(),
            {"amount": 100},
            resolve_user_variables({"user_id": "42"}),
        )


def test_apply_coerces_literal_types():
    schema = {
        "x-nanzi-system-params": [
            {"name": "applicant", "type": "string", "value": "fixed"},
            {"name": "tenant_id", "type": "integer", "value": "7"},
            {"name": "score", "type": "number", "value": "1.5"},
            {"name": "active", "type": "boolean", "value": "true"},
        ]
    }

    prepared = apply_system_params(schema, {}, {})

    assert prepared == {
        "applicant": "fixed",
        "tenant_id": 7,
        "score": 1.5,
        "active": True,
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
pytest tests/ai/tools/test_system_params.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.services.ai.tools.system_params'`

- [ ] **Step 3: 实现模块**

创建 `app/services/ai/tools/system_params.py`：

```python
"""API 工具的系统级参数。

系统级参数不进入模型可见的 ``properties``：它们由平台在发请求前按当前用户
强制写入，模型既看不到也改不了。配置存放在 ``parameter_schema`` 顶层扩展
``x-nanzi-system-params``，因此不需要数据库迁移。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.ai.tools.user_placeholders import replace_user_placeholders

SYSTEM_PARAMS_KEY = "x-nanzi-system-params"
SYSTEM_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SYSTEM_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean"})
_NON_PARAM_KEYS = frozenset({"type", "required", "$schema", "properties", "description"})


class SystemParamConfigError(ValueError):
    """系统级参数配置不合法。"""


@dataclass(frozen=True)
class SystemParamSpec:
    name: str
    type: str
    value: str
    description: str = ""


def _schema_mapping(parameter_schema: Any) -> dict[str, Any]:
    if parameter_schema is None:
        return {}
    if isinstance(parameter_schema, str):
        try:
            parsed = json.loads(parameter_schema or "{}")
        except (TypeError, ValueError) as exc:
            raise SystemParamConfigError("参数定义不是合法 JSON") from exc
    elif isinstance(parameter_schema, Mapping):
        parsed = dict(parameter_schema)
    else:
        raise SystemParamConfigError("参数定义必须是 JSON 对象")
    if not isinstance(parsed, dict):
        raise SystemParamConfigError("参数定义必须是 JSON 对象")
    return parsed


def _normal_properties(schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return properties
    return {
        str(key): value
        for key, value in schema.items()
        if not str(key).startswith("x-") and str(key) not in _NON_PARAM_KEYS
    }


def parse_system_params(parameter_schema: Any) -> list[SystemParamSpec]:
    schema = _schema_mapping(parameter_schema)
    raw = schema.get(SYSTEM_PARAMS_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SystemParamConfigError(f"{SYSTEM_PARAMS_KEY} 必须是数组")

    specs: list[SystemParamSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, Mapping):
            raise SystemParamConfigError(f"第 {index} 个系统级参数必须是对象")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise SystemParamConfigError(f"第 {index} 个系统级参数缺少名称")
        if not SYSTEM_PARAM_NAME_RE.fullmatch(name):
            raise SystemParamConfigError(f"系统级参数名不合法：{name}")
        if name in seen:
            raise SystemParamConfigError(f"系统级参数名重复：{name}")
        type_name = str(entry.get("type") or "string").strip()
        if type_name not in SYSTEM_PARAM_TYPES:
            raise SystemParamConfigError(f"系统级参数 {name} 的类型不支持：{type_name}")
        value = entry.get("value")
        if not isinstance(value, str) or not value.strip():
            raise SystemParamConfigError(f"系统级参数 {name} 缺少固定值")
        specs.append(SystemParamSpec(
            name=name,
            type=type_name,
            value=value,
            description=str(entry.get("description") or ""),
        ))
        seen.add(name)
    return specs


def validate_system_params(parameter_schema: Any) -> list[SystemParamSpec]:
    schema = _schema_mapping(parameter_schema)
    specs = parse_system_params(schema)
    properties = _normal_properties(schema)
    for spec in specs:
        if spec.name in properties:
            raise SystemParamConfigError(
                f"系统级参数 {spec.name} 与普通参数重名，请改名或删除普通参数"
            )
    return specs


def _coerce_literal(value: Any, type_name: str, name: str) -> Any:
    if not isinstance(value, str):
        return value
    try:
        if type_name == "integer":
            return int(value)
        if type_name == "number":
            return float(value)
        if type_name == "boolean":
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError
        return value
    except (TypeError, ValueError) as exc:
        raise SystemParamConfigError(
            f"系统级参数 {name} 的值 {value!r} 不能转换为 {type_name}"
        ) from exc


def apply_system_params(
    parameter_schema: Any,
    params: Mapping[str, Any],
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    specs = validate_system_params(parameter_schema)
    prepared = dict(params)
    for spec in specs:
        raw = replace_user_placeholders(spec.value, variables, expected_type=spec.type)
        prepared[spec.name] = _coerce_literal(raw, spec.type, spec.name)
    return prepared
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
pytest tests/ai/tools/test_system_params.py -q
```

Expected: all passed.

---

### Task 2: 接入运行时请求内核

**Files:**
- Modify: `app/services/ai/tools/generic_api.py`
- Test: `tests/ai/tools/test_system_params.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/ai/tools/test_system_params.py` 增加：

```python
import json
from unittest.mock import AsyncMock, patch

import httpx

from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory


def _tool(schema, *, method="POST", url="https://api.test/refund"):
    return SysApiTool(
        name="refund",
        method=method,
        url_template=url,
        headers=json.dumps({}),
        parameter_schema=schema,
    )


def test_system_params_are_not_in_model_schema():
    config = _tool(_schema())
    tool = GenericApiToolFactory.create_tool(config)

    assert "applicant" not in tool.args_schema.model_fields
    assert "applicant" not in json.dumps(
        tool.args_schema.model_json_schema(), ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_perform_request_injects_system_param_into_json_body():
    config = _tool(_schema())
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        request.return_value = httpx.Response(200, json={"ok": True})

        await GenericApiToolFactory._perform_request(
            config,
            {"amount": 100, "applicant": "mallory"},
            user_info=USER_INFO,
        )

        args, kwargs = request.call_args

    assert args[0] == "POST"
    assert kwargs["json"] == {"amount": 100, "applicant": "alice"}


@pytest.mark.asyncio
async def test_perform_request_injects_system_param_into_path():
    schema = {
        "properties": {},
        "x-nanzi-system-params": [
            {"name": "user_id", "type": "integer", "value": "{user.id}"}
        ],
    }
    config = _tool(
        schema,
        method="GET",
        url="https://api.test/users/{user_id}",
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        get.return_value = httpx.Response(200, json={"ok": True})

        await GenericApiToolFactory._perform_request(
            config,
            {},
            user_info=USER_INFO,
        )

        args, kwargs = get.call_args

    assert args[0] == "https://api.test/users/42"
    assert kwargs["params"] == {}


@pytest.mark.asyncio
async def test_flat_schema_extension_is_not_exposed_as_normal_param():
    schema = {
        "amount": {"type": "number"},
        "x-nanzi-system-params": [
            {"name": "applicant", "value": "{user.user_name}"}
        ],
    }
    tool = GenericApiToolFactory.create_tool(_tool(schema))

    assert "x-nanzi-system-params" not in tool.args_schema.model_fields
```

- [ ] **Step 2: 运行确认失败**

Run:

```bash
pytest tests/ai/tools/test_system_params.py -q
```

Expected: model schema test / injection tests fail, because runtime not wired.

- [ ] **Step 3: 修改 `generic_api.py`**

在导入区加入：

```python
from app.services.ai.tools.system_params import apply_system_params
```

把 `_parse_parameter_schema` 的扁平分支改为过滤扩展键：

```python
    properties = schema_def
    required_fields: set[str] = set()
    if "properties" in schema_def and isinstance(schema_def["properties"], dict):
        properties = schema_def["properties"]
        if "required" in schema_def and isinstance(schema_def["required"], list):
            required_fields = set(schema_def["required"])
    else:
        properties = {
            str(key): value
            for key, value in schema_def.items()
            if not str(key).startswith("x-")
            and str(key) not in {"type", "required", "$schema", "description"}
        }
    return schema_def, properties, required_fields
```

在 `_perform_request` 中，普通参数替换之后、URL 路径替换之前加入：

```python
        params = _apply_user_placeholders(config, params, variables)
        params = apply_system_params(config.parameter_schema, params, variables)

        url = config.url_template
```

- [ ] **Step 4: 运行确认通过**

Run:

```bash
pytest tests/ai/tools/test_system_params.py tests/ai/tools/test_user_placeholders.py tests/ai/tools/test_generic_api.py -q
```

Expected: all passed；普通参数和用户变量行为不回归。

---

### Task 3: 保存与试跑接口校验

**Files:**
- Modify: `app/api/portal/endpoints/tools.py`
- Modify: `app/schemas/tool.py`
- Test: `tests/api/portal/test_tool_system_params.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/api/portal/test_tool_system_params.py`：

```python
"""系统级参数在工具保存/试跑接口上的契约。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.ai.tools.generic_api import GenericApiToolFactory

PREVIEW_URL = "/api/portal/tools/preview"


def _preview_payload(**overrides):
    payload = {
        "method": "POST",
        "url_template": "https://api.test/refund",
        "headers": {},
        "parameter_schema": {
            "properties": {"amount": {"type": "number"}},
            "x-nanzi-system-params": [
                {"name": "applicant", "type": "string", "value": "{user.user_name}"}
            ],
        },
        "params": {"amount": 100},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_preview_rejects_invalid_system_param_config(client, admin_api_key):
    response = await client.post(
        PREVIEW_URL,
        json=_preview_payload(
            parameter_schema={
                "x-nanzi-system-params": [
                    {"name": "applicant", "value": ""}
                ]
            }
        ),
        headers={"X-API-Key": admin_api_key},
    )

    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "invalid_parameter_schema"
    assert "applicant" in body["message"]


@pytest.mark.asyncio
async def test_preview_does_not_require_system_param_in_url(client, admin_api_key):
    captured = {}

    async def fake(config, params, **kwargs):
        captured["params"] = params
        return (
            httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", "https://api.test")),
            3,
            config.url_template,
        )

    with patch.object(GenericApiToolFactory, "_perform_request", new=fake):
        response = await client.post(
            PREVIEW_URL,
            json=_preview_payload(url_template="https://api.test/users/{applicant}"),
            headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is True, body
    assert captured["params"] == {"amount": 100}
```

- [ ] **Step 2: 运行确认失败**

Run:

```bash
pytest tests/api/portal/test_tool_system_params.py -q
```

Expected: `invalid_parameter_schema` 不存在，且路径预检仍返回 `missing_param`。

- [ ] **Step 3: 修改接口与 schema**

在 `app/api/portal/endpoints/tools.py` 导入：

```python
from app.services.ai.tools.system_params import (
    SYSTEM_PARAMS_KEY,
    SystemParamConfigError,
    validate_system_params,
)
```

`create_tool` 在查重之后、写库之前加入：

```python
    try:
        validate_system_params(tool_in.parameter_schema)
    except SystemParamConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

`update_tool` 在 `update_data = tool_in.model_dump(exclude_unset=True)` 之前加入：

```python
    if "parameter_schema" in tool_in.model_fields_set:
        try:
            validate_system_params(tool_in.parameter_schema)
        except SystemParamConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
```

`preview_tool` 在现有 URL 占位符预检之前加入：

```python
    try:
        system_specs = validate_system_params(payload.parameter_schema)
    except SystemParamConfigError as exc:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="invalid_parameter_schema",
            message=str(exc),
        )
    system_param_names = {spec.name for spec in system_specs}
    unfilled = [
        name
        for name in _PLACEHOLDER_RE.findall(payload.url_template)
        if name not in system_param_names
        and (params.get(name) is None or not str(params.get(name)).strip())
    ]
```

注意：`unfilled` 的原有列表推导式要被上面的版本替换，不能变成两份判断。

在 `app/schemas/tool.py` 的 `error_kind` 描述中加入：

```python
            "missing_param / invalid_parameter_schema / "
```

- [ ] **Step 4: 运行确认通过**

Run:

```bash
pytest tests/api/portal/test_tool_system_params.py tests/api/portal/test_tool_preview.py -q
```

Expected: all passed.

---

### Task 4: 前端 schema 解析、序列化与校验

**Files:**
- Modify: `frontend/src/utils/toolSchema.ts`
- Test: `tests/frontend/tool_schema_system_params.mjs`

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/tool_schema_system_params.mjs`：

```javascript
import assert from 'node:assert/strict'
import {
  parseToolSchema,
  serializeToolSchema,
  validateSystemParams,
  SYSTEM_PARAMS_KEY,
} from '../../frontend/src/utils/toolSchema.ts'

let passed = 0
const check = (label, fn) => { fn(); passed += 1; console.log('  ✓ ' + label) }

const schema = {
  type: 'object',
  properties: { amount: { type: 'number' } },
  required: ['amount'],
  'x-nanzi-system-params': [
    { name: 'applicant', type: 'string', value: '{user.user_name}', description: '申请人' },
  ],
  'x-other': { keep: true },
}

check('解析系统级参数且不放进普通参数', () => {
  const parsed = parseToolSchema(JSON.stringify(schema))
  assert.equal(parsed.systemParams.length, 1)
  assert.equal(parsed.systemParams[0].name, 'applicant')
  assert.deepEqual(parsed.params.map((p) => p.name), ['amount'])
})

check('系统级参数往返保留，required 不再引用隐藏参数', () => {
  const parsed = parseToolSchema(JSON.stringify({
    ...schema,
    required: ['amount', 'applicant'],
  }))
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.deepEqual(out[SYSTEM_PARAMS_KEY], schema['x-nanzi-system-params'])
  assert.deepEqual(out.required, ['amount'])
  assert.equal(out.properties.applicant, undefined)
  assert.deepEqual(out['x-other'], { keep: true })
})

check('扁平形态保持扁平，扩展不被当成普通参数', () => {
  const flat = {
    amount: { type: 'number' },
    [SYSTEM_PARAMS_KEY]: [
      { name: 'applicant', type: 'string', value: '{user.user_name}', description: '' },
    ],
  }
  const parsed = parseToolSchema(JSON.stringify(flat))
  assert.equal(parsed.shape, 'flat')
  assert.deepEqual(parsed.params.map((p) => p.name), ['amount'])
  const out = JSON.parse(serializeToolSchema(parsed))
  assert.equal(out.amount.type, 'number')
  assert.equal(out[SYSTEM_PARAMS_KEY][0].name, 'applicant')
})

check('校验拦截空名、重名、普通参数冲突、空值', () => {
  const errors = validateSystemParams([
    { name: '', type: 'string', value: 'x', description: '' },
    { name: 'applicant', type: 'string', value: '', description: '' },
    { name: 'applicant', type: 'string', value: 'x', description: '' },
    { name: 'amount', type: 'string', value: 'x', description: '' },
  ], [{ name: 'amount' }])
  assert.equal(errors.some((e) => e.includes('缺少名称')), true)
  assert.equal(errors.some((e) => e.includes('重复')), true)
  assert.equal(errors.some((e) => e.includes('重名')), true)
  assert.equal(errors.some((e) => e.includes('固定值')), true)
})

console.log(`\n系统级参数 schema 测试全部通过：${passed} 项`)
```

- [ ] **Step 2: 运行确认失败**

Run:

```bash
node --experimental-strip-types tests/frontend/tool_schema_system_params.mjs
```

Expected: import 报错，`SYSTEM_PARAMS_KEY` 不存在。

- [ ] **Step 3: 修改 `toolSchema.ts`**

增加类型与常量：

```ts
export const SYSTEM_PARAMS_KEY = 'x-nanzi-system-params'
export const SYSTEM_PARAM_TYPES = ['string', 'integer', 'number', 'boolean'] as const
export type SystemParamType = typeof SYSTEM_PARAM_TYPES[number]
export type SystemParam = {
  name: string
  type: SystemParamType
  value: string
  description: string
}
```

`ParsedToolSchema` 增加：

```ts
  systemParams: SystemParam[]
```

在 `parseToolSchema` 中：

```ts
    const rawSystemParams = parsed[SYSTEM_PARAMS_KEY]
    const systemParams: SystemParam[] = Array.isArray(rawSystemParams)
      ? rawSystemParams.map((entry: any) => ({
          name: typeof entry?.name === 'string' ? entry.name : '',
          type: SYSTEM_PARAM_TYPES.includes(entry?.type) ? entry.type : 'string',
          value: typeof entry?.value === 'string' ? entry.value : '',
          description: typeof entry?.description === 'string' ? entry.description : '',
        }))
      : []
```

在 `rest` 循环中，标准形态和扁平形态都跳过合法的系统级参数数组：

```ts
        if (key === SYSTEM_PARAMS_KEY && Array.isArray(value)) continue
```

放在 `if (standard)` 分支之前，两个形态共用。

`return` 改为：

```ts
    return { shape, hadRequiredList: requiredList !== null, rest, params, systemParams }
```

在 `serializeToolSchema` 中：

```ts
    const systemNames = new Set(
      schema.systemParams.map((param) => param.name.trim()).filter(Boolean),
    )
```

必填收集改为：

```ts
        if (param.required && !systemNames.has(name)) {
            requiredNames.push(name)
        } else if (!param.required) {
```

注意：原来的 `if (param.required) { ... } else { ... }` 要改成上面的 `else if (!param.required)`，避免系统级同名参数误走普通参数分支。

在 `payload` 赋值后加入：

```ts
    if (schema.systemParams.length > 0) {
        payload[SYSTEM_PARAMS_KEY] = schema.systemParams
    }
```

增加校验函数：

```ts
export const validateSystemParams = (
    systemParams: SystemParam[],
    params: Array<Pick<SchemaParam, 'name'>>,
): string[] => {
    const errors: string[] = []
    const seen = new Set<string>()
    const normalNames = new Set(params.map((param) => param.name.trim()).filter(Boolean))

    systemParams.forEach((param, index) => {
        const name = param.name.trim()
        if (!name) {
            errors.push(`第 ${index + 1} 个系统级参数缺少名称`)
        } else if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
            errors.push(`系统级参数名不合法：${name}`)
        } else if (seen.has(name)) {
            errors.push(`系统级参数名重复：${name}`)
        } else if (normalNames.has(name)) {
            errors.push(`系统级参数 ${name} 与普通参数重名`)
        }
        seen.add(name)
        if (!SYSTEM_PARAM_TYPES.includes(param.type)) {
            errors.push(`系统级参数 ${name || index + 1} 的类型不支持：${param.type}`)
        }
        if (!param.value.trim()) {
            errors.push(`系统级参数 ${name || index + 1} 缺少固定值`)
        }
    })
    return errors
}
```

- [ ] **Step 4: 运行确认通过**

Run:

```bash
node --experimental-strip-types tests/frontend/tool_schema_system_params.mjs
node --experimental-strip-types tests/frontend/tool_schema_roundtrip.mjs
```

Expected: both pass.

---

### Task 5: 前端系统级参数编辑器

**Files:**
- Create: `frontend/src/components/system/ToolSystemParamEditor.vue`
- Modify: `frontend/src/components/system/ToolParamEditor.vue`
- Test: `tests/frontend/test_api_tool_system_params_contract.py`

- [ ] **Step 1: 写失败契约测试**

创建 `tests/frontend/test_api_tool_system_params_contract.py`：

```python
"""系统级参数编辑器与试跑提示的源码契约。"""
from pathlib import Path

PARAM_EDITOR = Path("frontend/src/components/system/ToolParamEditor.vue")
SYSTEM_EDITOR = Path("frontend/src/components/system/ToolSystemParamEditor.vue")
TESTER = Path("frontend/src/components/system/ApiToolTester.vue")
REGISTRY = Path("frontend/src/components/system/ToolRegistry.vue")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def test_param_editor_embeds_system_param_editor():
    source = _source(PARAM_EDITOR)
    assert "ToolSystemParamEditor" in source
    assert "systemParams" in source
    assert "validateSystemParams" in source


def test_system_editor_has_name_type_value_description_and_variable_picker():
    source = _source(SYSTEM_EDITOR)
    for field in ("p.name", "p.type", "p.value", "p.description"):
        assert field in source
    assert "PermissionVariableMenu" in source
    assert "添加系统级参数" in source
    assert "模型不可见" in source


def test_save_path_blocks_invalid_system_params():
    source = _source(REGISTRY)
    assert "validateSystemParams" in source
    assert "parseToolSchema" in source
    assert "showToast" in source


def test_tester_shows_system_params_are_injected_not_entered():
    source = _source(TESTER)
    assert "systemParams" in source
    assert "平台自动注入" in source
    assert "systemParams.length" in source
```

- [ ] **Step 2: 运行确认失败**

Run:

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_api_tool_system_params_contract.py -q
```

Expected: 缺少 `ToolSystemParamEditor.vue`，测试失败。

- [ ] **Step 3: 创建 `ToolSystemParamEditor.vue`**

实现一个受控列表组件。核心脚本：

```vue
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import PermissionVariableMenu from '../metadata/PermissionVariableMenu.vue'
import {
  SYSTEM_PARAM_TYPES,
  type SystemParam,
  type SystemParamType,
} from '../../utils/toolSchema'
import type { UserVariable } from '../../utils/userVariables'

const props = defineProps<{
  modelValue: SystemParam[]
  extraDataVariables: UserVariable[]
  builtinVariables: UserVariable[]
}>()

const emit = defineEmits<{ (e: 'update:modelValue', value: SystemParam[]): void }>()

const openMenuIndex = ref<number | null>(null)

const update = (index: number, patch: Partial<SystemParam>) => {
  emit('update:modelValue', props.modelValue.map((param, i) => (
    i === index ? { ...param, ...patch } : param
  )))
}

const addParam = () => {
  emit('update:modelValue', [
    ...props.modelValue,
    { name: '', type: 'string', value: '', description: '' },
  ])
}

const removeParam = (index: number) => {
  openMenuIndex.value = null
  emit('update:modelValue', props.modelValue.filter((_, i) => i !== index))
}

const selectVariable = (index: number, value: string) => {
  update(index, { value })
  openMenuIndex.value = null
}

const onDocumentClick = () => { openMenuIndex.value = null }
onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>
```

模板包含：

- 标题 `系统级参数（模型不可见）`；
- 每行参数名 input、类型 select、固定值 input、描述 input、`{}` 变量按钮、删除按钮；
- 变量按钮打开 `PermissionVariableMenu`，选择后写入 `value`；
- `+ 添加系统级参数`；
- 说明文字：`这些参数不会出现在模型工具定义里，调用前由平台强制写入。`

`ToolParamEditor.vue` 修改：

```ts
import ToolSystemParamEditor from './ToolSystemParamEditor.vue'
import { validateSystemParams, type SystemParam } from '../../utils/toolSchema'
```

增加状态：

```ts
const systemParams = ref<SystemParam[]>([])
```

`loadVisualFromJson` 中：

```ts
    systemParams.value = parsed.systemParams.map((param) => ({ ...param }))
```

`buildSchemaJson` 中：

```ts
    systemParams: systemParams.value,
```

模板中，在普通参数列表的 `+ 添加参数` 之后加入：

```vue
      <ToolSystemParamEditor
        v-model="systemParams"
        :builtin-variables="BUILTIN_USER_VARIABLES"
        :extra-data-variables="extraDataVariables"
      />
```

增加回写 watch：

```ts
watch(systemParams, () => {
    if (schemaEditorMode.value !== 'visual') return
    const next = buildSchemaJson()
    if (next !== props.modelValue) emit('update:modelValue', next)
}, { deep: true })
```

更新帮助文案：把原来“取消必填 + 默认值”的说明改为：

```text
需要模型绝对不能改的值（申请人、操作人），请使用下方「系统级参数」；
普通参数的默认值只是兜底，模型显式传值时仍会覆盖。
```

- [ ] **Step 4: 运行确认通过**

Run:

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_api_tool_system_params_contract.py tests/frontend/test_api_tool_schema_editor_contract.py -q
```

Expected: all passed.

---

### Task 6: 保存校验与试跑提示

**Files:**
- Modify: `frontend/src/components/system/ToolRegistry.vue`
- Modify: `frontend/src/components/system/ApiToolTester.vue`
- Test: `tests/frontend/test_api_tool_system_params_contract.py`

- [ ] **Step 1: 在 `ToolRegistry.saveTool` 加入校验**

在 `saveTool` 的 schema JSON 解析成功之后加入：

```ts
        const parsed = parseToolSchema(toolForm.value.parameter_schema_str)
        if (parsed) {
            const systemParamErrors = validateSystemParams(
                parsed.systemParams,
                parsed.params,
            )
            if (systemParamErrors.length > 0) {
                showToast(systemParamErrors[0], 'error')
                return
            }
        }
```

导入：

```ts
import { parseToolSchema, validateSystemParams } from '../../utils/toolSchema'
```

- [ ] **Step 2: 在 `ApiToolTester` 增加只读提示与错误分支**

`params` 计算之后增加：

```ts
const systemParams = computed(() => parseToolSchema(props.schemaStr)?.systemParams ?? [])
```

在参数输入区下方加入：

```vue
    <p
      v-if="systemParams.length > 0"
      class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-600"
    >
      系统级参数：{{ systemParams.map((param) => param.name).join('、') }}
      （平台自动注入，无需填写）
    </p>
```

错误展示分支加入：

```vue
        <span v-else-if="result.error_kind === 'invalid_parameter_schema'">
          {{ result.message }}
        </span>
```

并把下面 message 的排除条件加上该类型：

```vue
v-if="result.message && !result.ok && result.error_kind !== 'missing_param' && result.error_kind !== 'unsupported_method' && result.error_kind !== 'missing_user_variable' && result.error_kind !== 'invalid_parameter_schema'"
```

- [ ] **Step 3: 运行前端契约与类型检查**

Run:

```bash
pytest --confcutdir=tests/frontend tests/frontend/test_api_tool_system_params_contract.py -q
cd frontend && npx vue-tsc --noEmit
```

Expected: contract pass；TypeScript 无错误。

---

### Task 7: 清单与整体验证

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 更新交付清单**

在 `tests/CHECKLIST.md` 增加一行（保持 5 列）：

```markdown
| API 工具系统级参数 (API Tool System Params) | 参数编辑器新增模型不可见的系统级参数；运行时支持 body/query/path 强制注入与用户变量替换 | `tests/ai/tools/test_system_params.py`、`tests/api/portal/test_tool_system_params.py`、`tests/frontend/tool_schema_system_params.mjs`、`tests/frontend/test_api_tool_system_params_contract.py` | `pytest tests/ai/tools/test_system_params.py tests/api/portal/test_tool_system_params.py -q`；`node --experimental-strip-types tests/frontend/tool_schema_system_params.mjs`；`vue-tsc --noEmit` | ✅ 已完成 |
```

- [ ] **Step 2: 跑后端 targeted tests**

Run:

```bash
pytest tests/ai/tools/test_system_params.py \
  tests/ai/tools/test_user_placeholders.py \
  tests/ai/tools/test_generic_api.py \
  tests/api/portal/test_tool_system_params.py \
  tests/api/portal/test_tool_preview.py -q
```

Expected: all passed.

- [ ] **Step 3: 跑前端 targeted tests**

Run:

```bash
node --experimental-strip-types tests/frontend/tool_schema_system_params.mjs
node --experimental-strip-types tests/frontend/tool_schema_roundtrip.mjs
pytest --confcutdir=tests/frontend \
  tests/frontend/test_api_tool_system_params_contract.py \
  tests/frontend/test_api_tool_schema_editor_contract.py -q
cd frontend && npx vue-tsc --noEmit
```

Expected: all passed，TypeScript 无错误。

- [ ] **Step 4: 检查 diff 质量**

Run:

```bash
git diff --check
git status --short
```

Expected: 无空白错误；只出现本计划涉及的文件。

---

## 自检

- **Spec coverage:** 数据结构、前端解析/序列化、编辑器、后端注入、保存/试跑校验、错误处理、测试、验收标准均有对应任务。
- **Placeholder scan:** 没有 TBD/TODO；代码块包含实际签名与关键实现。
- **Type consistency:** 前端统一使用 `SystemParam` / `SystemParamType` / `SYSTEM_PARAMS_KEY`；后端统一使用 `SystemParamSpec` / `SystemParamConfigError` / `apply_system_params`。
- **Spec gap fixed:** 试跑接口的 URL 未填预检必须排除系统级参数，已写入 Task 3。
- **Project rule:** 计划不包含自动 commit；完成后由用户决定提交。
