# API 工具「向导模式」Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **本项目偏差（AGENTS.md 优先）：** 每个任务末尾**不执行 `git commit`**——本项目规定 Agent 不得自行提交。任务收尾一律是「跑测试 + 记录改动文件」，由用户决定何时提交。Plan 里任何 `git add/commit` 意图都由你口头汇报替代。

**Goal:** 让不熟悉 API 的管理员能从一条真实请求（URL 或 curl）出发，经 4 步引导配出并**试跑通过**一个 HTTP 工具，全程不需要理解 `{param}` 与 JSON Schema。

**Architecture:** 弹窗内新增「向导模式」tab，与既有「表单模式」**共用同一份 `toolForm`**（不引入第二个数据模型）。参数编辑区抽成 `ToolParamEditor.vue` 供两边共用，数据逻辑仍全部走既有的 `utils/toolSchema.ts`。新增两个纯函数模块（`toolImport.ts` 解析、`toolDraft.ts` 判落位与必填），一个试跑面板 `ApiToolTester.vue`，以及后端 `POST /api/portal/tools/preview`（草稿不落库，请求内核与运行时**同一段代码**）。

**Tech Stack:** Vue 3 + TypeScript + Vite + Tailwind；FastAPI + Pydantic 2 + httpx；pytest（含 `node --experimental-strip-types` 真跑纯函数）。

**Spec:** `docs/superpowers/specs/2026-10-15-api-tool-wizard-design.md`
**设计稿:** `prototypes/2026-10-15-api-tool-wizard.html`

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `frontend/src/utils/toolMethods.ts`（新） | 平台支持的 HTTP 方法唯一来源 |
| `frontend/src/utils/toolImport.ts`（新） | 把 URL / curl 文本解析成结构化请求；不碰 DOM 与状态 |
| `frontend/src/utils/toolDraft.ts`（新） | 由解析结果生成参数清单（落位 + 必填建议 + 类型推断） |
| `frontend/src/components/system/ToolParamEditor.vue`（新） | 参数编辑区，两个 tab 共用 |
| `frontend/src/components/system/ApiToolTester.vue`（新） | 试跑面板 |
| `frontend/src/components/system/ToolWizard.vue`（新） | 4 步壳，只负责写 `toolForm` |
| `frontend/src/components/system/ToolRegistry.vue`（改） | 加 tab、改用 `ToolParamEditor`、收敛方法清单 |
| `frontend/src/api/tool.ts`（改） | 新增 `preview` |
| `app/services/ai/tools/generic_api.py`（改） | 抽出 `_perform_request` 内核，运行时输出逐字节不变 |
| `app/schemas/tool.py`（改） | preview 请求/响应模型 |
| `app/api/portal/endpoints/tools.py`（改） | 新增 `POST /preview` |

---

## Task 0: 采集基线

**Files:** 无（只跑命令）

- [ ] **Step 1: 前端契约测试基线**

Run: `.venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3`
Expected: 形如 `1411 passed, 1 skipped`。**把这个数字抄下来**，它是本特性所有「零回归」判断的基准（写进 CHECKLIST）。

- [ ] **Step 2: 类型检查基线**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | tail -3`
Expected: 输出若干既有错误（本仓库当前有存量错误）。**把错误总数抄下来**；后续只比较「新增/消失」，不比总数。

- [ ] **Step 3: 后端基线**

Run: `.venv/bin/python -m pytest tests/api/portal -q 2>&1 | tail -3`
Expected: 全绿。抄下数字。

---

## Task 1: 收敛 HTTP 方法清单

**Files:**
- Create: `frontend/src/utils/toolMethods.ts`
- Create: `tests/frontend/test_api_tool_method_list_contract.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/frontend/test_api_tool_method_list_contract.py`：

```python
"""API 工具「HTTP 方法」清单一致性契约（静态源码断言）。

背景：同一条方法清单在仓库里曾经各说各话——
  * 表单创建/编辑下拉：GET/POST/PUT/DELETE（**少了 PATCH**）；
  * 列表筛选器：再加 PATCH/HEAD/OPTIONS（**多了运行时根本不支持的两个**）；
  * 运行时 ``GenericApiToolFactory._execute_request``：GET 特判 + POST/PUT/PATCH/DELETE，
    HEAD/OPTIONS 会直接返回 ``[Error] Unsupported method``。

后果：接口文档里极常见的 ``curl -X PATCH`` 在表单里**无处安放**；而筛选器里的
HEAD/OPTIONS 是永远筛不出结果的死选项。本契约把清单收敛到 ``utils/toolMethods.ts``
单一来源，并钉住它与运行时一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

METHODS_UTIL = Path("frontend/src/utils/toolMethods.ts")
REGISTRY = Path("frontend/src/components/system/ToolRegistry.vue")
GENERIC_API = Path("app/services/ai/tools/generic_api.py")


def _source(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def test_methods_util_is_the_single_source():
    source = _source(METHODS_UTIL)
    assert "SUPPORTED_HTTP_METHODS" in source
    assert "isSupportedMethod" in source


def test_static_list_matches_runtime_support():
    """前端静态清单必须等于运行时真正支持的方法集合。"""
    util = _source(METHODS_UTIL)
    match = re.search(r"SUPPORTED_HTTP_METHODS\s*=\s*\[([^\]]*)\]", util)
    assert match, "未找到 SUPPORTED_HTTP_METHODS 数组字面量"
    frontend = [item.strip().strip("'\"").upper() for item in match.group(1).split(",") if item.strip()]

    runtime = _source(GENERIC_API)
    # 运行时把 GET 单独一条分支，其余四个走同一个 request 分支
    body_branch = re.search(r"method in \[([^\]]*)\]", runtime)
    assert body_branch, "未找到运行时的非 GET 方法分支"
    runtime_methods = [item.strip().strip("'\"").upper() for item in body_branch.group(1).split(",") if item.strip()]

    assert frontend == ["GET"] + runtime_methods, (
        f"前端清单 {frontend} 与运行时 {'GET' + runtime_methods} 不一致"
    )


def test_static_list_excludes_unsupported_methods():
    util = _source(METHODS_UTIL)
    match = re.search(r"SUPPORTED_HTTP_METHODS\s*=\s*\[([^\]]*)\]", util)
    assert match
    static = [item.strip().strip("'\"").upper() for item in match.group(1).split(",") if item.strip()]
    # 运行时对这两个方法返回 [Error] Unsupported method，配了也永远失败
    assert "HEAD" not in static
    assert "OPTIONS" not in static
    # curl -X PATCH 是最常见的写法之一，必须可选
    assert "PATCH" in static


def test_registry_form_dropdown_uses_shared_list():
    source = _source(REGISTRY)
    assert "SUPPORTED_HTTP_METHODS" in source
    # 下拉不再各写一份字面量清单
    assert 'v-for="method in SUPPORTED_HTTP_METHODS"' in source


def test_registry_filter_keeps_data_driven_extras():
    """筛选器静态项收敛到共享清单，但必须保留「并上既有数据里出现过的方法」。

    后端 method 是裸 str、无枚举校验，历史库里可能有 HEAD 等异常数据；
    删掉这一段会让那些行再也筛不出来。
    """
    source = _source(REGISTRY)
    body = source[source.index("const toolMethodOptions"):]
    body = body[: body.index("])") + 2]
    assert "SUPPORTED_HTTP_METHODS" in body
    assert "tools.value.map" in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_method_list_contract.py -q`
Expected: FAIL，报「缺少文件：frontend/src/utils/toolMethods.ts」

- [ ] **Step 3: 实现 toolMethods.ts**

创建 `frontend/src/utils/toolMethods.ts`：

```ts
/**
 * 平台支持的 HTTP 方法**唯一来源**。
 *
 * 与运行时 ``GenericApiToolFactory._execute_request`` 的支持集合严格一致：
 * GET 走 query，POST/PUT/PATCH/DELETE 走 JSON body，其余方法运行时直接返回
 * ``[Error] Unsupported method``——所以 HEAD/OPTIONS **不能**出现在静态可选项里，
 * 配了也永远失败（历史遗留的死选项，本次一并删掉）。
 *
 * 契约测试 ``tests/frontend/test_api_tool_method_list_contract.py`` 钉住：
 * 本清单 == 运行时支持集合。
 */
export const SUPPORTED_HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const

export type SupportedHttpMethod = (typeof SUPPORTED_HTTP_METHODS)[number]

/** 方法是否被运行时支持（大小写不敏感；空值视为不支持）。 */
export const isSupportedMethod = (method: string | null | undefined): boolean =>
    SUPPORTED_HTTP_METHODS.includes(
        String(method || '').trim().toUpperCase() as SupportedHttpMethod,
    )
```

- [ ] **Step 4: 改 ToolRegistry 的两处清单**

`frontend/src/components/system/ToolRegistry.vue`：

1. 在 `<script setup>` 的 import 区（第 3-9 行 `toolSchema` 那段之后）加：

```ts
import { SUPPORTED_HTTP_METHODS } from '../../utils/toolMethods'
```

2. 把第 206-209 行的 `toolMethodOptions` 改为：

```ts
// 静态项来自共享清单（不再各写一份）；
// 再并上「既有数据里真正出现过的方法」——后端 method 无枚举校验，
// 历史库里的异常值（如 HEAD）若不在选项里就再也筛不出来。
const toolMethodOptions = computed(() => Array.from(new Set<string>([
    ...SUPPORTED_HTTP_METHODS,
    ...tools.value.map((tool) => String(tool.method || '').toUpperCase()).filter(Boolean),
])))
```

3. 把第 645-650 行的表单方法下拉改为：

```html
                           <select v-model="toolForm.method" class="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-primary focus:border-primary sm:text-sm">
                               <option v-for="method in SUPPORTED_HTTP_METHODS" :key="method" :value="method">{{ method }}</option>
                           </select>
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_method_list_contract.py -q`
Expected: 5 passed

- [ ] **Step 6: 跑受影响的全量前端测试**

Run: `.venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3`
Expected: 与 Task 0 基线相比**只多 5 条**，无失败。

---

## Task 2: `toolImport.ts`——URL / curl 解析

**Files:**
- Create: `frontend/src/utils/toolImport.ts`
- Create: `tests/frontend/tool_import_roundtrip.mjs`
- Create: `tests/frontend/test_api_tool_import_contract.py`

- [ ] **Step 1: 写失败测试（Node 真跑）**

创建 `tests/frontend/tool_import_roundtrip.mjs`：

```js
import assert from 'node:assert/strict'
import { parseRequestInput } from '../../frontend/src/utils/toolImport.ts'

let passed = 0
const check = (label, fn) => { fn(); passed += 1; console.log('  ✓ ' + label) }

check('curl：多行续行 + -X + 两个 -H + -d JSON 体', () => {
  const r = parseRequestInput(`curl -X POST 'https://api.example.com/v1/orders/{order_id}/refund' \\
  -H 'Authorization: Bearer sk-xxx' \\
  -H 'Content-Type: application/json' \\
  -d '{"amount": 100, "reason": "客户申请"}'`)
  assert.equal(r.error, undefined)
  assert.equal(r.method, 'POST')
  assert.equal(r.urlTemplate, 'https://api.example.com/v1/orders/{order_id}/refund')
  assert.deepEqual(r.pathParams, ['order_id'])
  assert.equal(r.headers['Authorization'], 'Bearer sk-xxx')
  assert.equal(r.headers['Content-Type'], 'application/json')
  assert.deepEqual(
    r.bodyParams.map((p) => [p.name, p.value, p.type]),
    [['amount', '100', 'number'], ['reason', '客户申请', 'string']],
  )
})

check('curl：-d 在 -G 之外的单行写法与 --url', () => {
  const r = parseRequestInput(`curl --url https://x.test/a -X PUT -H "X-K: v" -d '{"n": 1}'`)
  assert.equal(r.method, 'PUT')
  assert.equal(r.urlTemplate, 'https://x.test/a')
  assert.equal(r.headers['X-K'], 'v')
  assert.equal(r.bodyParams[0].name, 'n')
})

check('curl：默认方法为 GET', () => {
  const r = parseRequestInput(`curl 'https://x.test/list?a=1'`)
  assert.equal(r.method, 'GET')
  assert.deepEqual(r.queryParams.map((p) => [p.name, p.value]), [['a', '1']])
})

check('curl：-d 非 JSON 体不猜，降级为警告', () => {
  const r = parseRequestInput(`curl -X POST https://x.test/a -d 'a=1&b=2'`)
  assert.equal(r.error, undefined)
  assert.equal(r.bodyParams.length, 0)
  assert.ok(r.warnings.some((w) => w.includes('请求体')))
})

check('纯 URL：query 与占位符', () => {
  const r = parseRequestInput('https://api.example.com/v1/weather?city={city}&days=3&units=metric')
  assert.equal(r.method, 'GET')
  assert.equal(r.urlTemplate, 'https://api.example.com/v1/weather')
  assert.deepEqual(r.queryParams.map((p) => [p.name, p.value]), [
    ['city', '{city}'], ['days', '3'], ['units', 'metric'],
  ])
})

check('query：无值、重复参数、百分号编码都能读出', () => {
  const r = parseRequestInput('https://x.test/s?flag&tag=a&tag=b&q=%E5%8C%97%E4%BA%AC')
  assert.deepEqual(r.queryParams.map((p) => [p.name, p.value]), [
    ['flag', ''], ['tag', 'a'], ['tag', 'b'], ['q', '北京'],
  ])
})

check('非法百分号编码不抛异常，保留原文', () => {
  const r = parseRequestInput('https://x.test/s?q=100%zz')
  assert.equal(r.error, undefined)
  assert.equal(r.queryParams[0].value, '100%zz')
})

check('缺 scheme 时补 https:// 并给警告', () => {
  const r = parseRequestInput('api.example.com/v1/x?a=1')
  assert.equal(r.error, undefined)
  assert.equal(r.urlTemplate, 'https://api.example.com/v1/x')
  assert.ok(r.warnings.some((w) => w.includes('https://')))
})

check('识别不出地址时返回 error，绝不抛异常', () => {
  assert.ok(parseRequestInput('').error)
  assert.ok(parseRequestInput('随便一段中文').error)
  assert.ok(parseRequestInput('curl -X POST').error)
})

check('URL 里 path 与 query 都出现的占位符都进 pathParams', () => {
  const r = parseRequestInput('https://x.test/{a}/y?b={b}&c=3')
  assert.deepEqual(r.pathParams, ['a'])
  assert.deepEqual(r.queryParams.map((p) => [p.name, p.value]), [['b', '{b}'], ['c', '3']])
})

console.log(`\n工具导入往返测试全部通过（${passed} 项）`)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --experimental-strip-types --no-warnings tests/frontend/tool_import_roundtrip.mjs`
Expected: FAIL，`Cannot find module .../toolImport.ts`

- [ ] **Step 3: 实现 toolImport.ts**

创建 `frontend/src/utils/toolImport.ts`：

```ts
/**
 * 把「接口文档里的一条请求」解析成结构化数据——支持完整 URL 与 curl 命令。
 *
 * 刻意只做前端纯函数：可以用 ``node --experimental-strip-types`` 直接跑往返测试
 * （见 ``tests/frontend/tool_import_roundtrip.mjs``），不碰 DOM、不碰组件状态、
 * 不新增后端端点。
 *
 * curl 只取 ``-X`` / ``--request`` / ``-H`` / ``--header`` / ``-d`` / ``--data`` /
 * ``--data-raw`` / ``--data-binary`` / ``--url``，**不做完整 shell 语法解析**：
 * 认不出的部分降级为 warning 并保留原文，绝不猜。
 */
import { isSupportedMethod, SUPPORTED_HTTP_METHODS } from './toolMethods'

export type ParsedQueryParam = { name: string; value: string }
export type ParsedBodyParam = { name: string; value: string; type: string }

export type ParsedRequest = {
    method: string
    /** 去掉 query 后的地址模板，保留 ``{param}`` 占位符 */
    urlTemplate: string
    /** 出现在 ``{...}`` 占位符里的参数名（URL 的 path 或 query 里都算） */
    pathParams: string[]
    queryParams: ParsedQueryParam[]
    bodyParams: ParsedBodyParam[]
    headers: Record<string, string>
    warnings: string[]
}

export type ParseFailure = { error: string }

const URL_RE = /^([a-zA-Z][a-zA-Z0-9+.\-]*:\/\/[^/?#]+)([^?#]*)(?:\?([^#]*))?(?:#.*)?$/
const PLACEHOLDER_RE = /\{([^{}/]+)\}/g
const BODY_OPTION_FLAGS = ['-d', '--data', '--data-raw', '--data-binary', '--data-ascii']

const decodeSafely = (raw: string): string => {
    try {
        return decodeURIComponent(raw)
    } catch {
        // 非法百分号编码（``100%zz``）不该让整个解析失败
        return raw
    }
}

const unwrapPlaceholder = (value: string): string | null => {
    const match = value.match(/^\{([^{}]+)\}$/)
    return match ? match[1] : null
}

/** 极简 shell 分词：支持单/双引号、反斜杠续行与转义。 */
export const tokenizeShellLike = (text: string): string[] => {
    const tokens: string[] = []
    let current = ''
    let quote: '"' | "'" | null = null
    let touched = false

    for (let i = 0; i < text.length; i += 1) {
        const char = text[i]
        if (quote) {
            if (char === quote) {
                quote = null
                continue
            }
            if (char === '\\' && quote === '"') {
                const next = text[i + 1]
                if (next === '"' || next === '\\') {
                    current += next
                    i += 1
                    continue
                }
            }
            current += char
            continue
        }
        if (char === '"' || char === "'") {
            quote = char
            touched = true
            continue
        }
        if (char === '\\') {
            const next = text[i + 1]
            if (next === '\n') { i += 1; continue }            // 续行
            if (next === '\r' && text[i + 2] === '\n') { i += 2; continue }
            if (next === undefined) continue
            current += next
            i += 1
            touched = true
            continue
        }
        if (/\s/.test(char)) {
            if (touched || current) {
                tokens.push(current)
                current = ''
                touched = false
            }
            continue
        }
        current += char
        touched = true
    }
    if (touched || current) tokens.push(current)
    return tokens
}

const inferBodyType = (value: unknown): string => {
    if (typeof value === 'number') return Number.isInteger(value) ? 'integer' : 'number'
    if (typeof value === 'boolean') return 'boolean'
    if (Array.isArray(value)) return 'array'
    if (value && typeof value === 'object') return 'object'
    return 'string'
}

const stringifyBodyValue = (value: unknown): string => {
    if (value === null || value === undefined) return ''
    if (typeof value === 'string') return value
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
}

/** 解析 URL（可含 ``{param}`` 占位符）。 */
const parseUrl = (
    raw: string,
    warnings: string[],
): { urlTemplate: string; pathParams: string[]; queryParams: ParsedQueryParam[] } | ParseFailure => {
    let text = raw.trim()
    if (!text) return { error: '没有可解析的内容' }

    let match = text.match(URL_RE)
    if (!match && /^[^\s/]+\.[^\s/]+(\/|$|\?)/.test(text)) {
        // 像域名但没写 scheme：补 https:// 并明确告知（不静默改）
        text = `https://${text}`
        warnings.push('地址没写协议，已按 https:// 处理——如果该接口只支持 http，请手动改回')
        match = text.match(URL_RE)
    }
    if (!match) {
        return { error: '没认出这是一个网址。请粘贴形如 https://api.example.com/v1/xxx 的完整地址' }
    }

    const [, origin, path, queryString] = match
    const urlTemplate = `${origin}${path || ''}`

    const pathParams: string[] = []
    for (const found of `${path || ''}`.matchAll(PLACEHOLDER_RE)) {
        if (!pathParams.includes(found[1])) pathParams.push(found[1])
    }

    const queryParams: ParsedQueryParam[] = []
    if (queryString) {
        for (const pair of queryString.split('&')) {
            if (!pair) continue
            const index = pair.indexOf('=')
            if (index === -1) {
                queryParams.push({ name: decodeSafely(pair), value: '' })
                continue
            }
            queryParams.push({
                name: decodeSafely(pair.slice(0, index)),
                value: decodeSafely(pair.slice(index + 1)),
            })
        }
    }
    // query 里的 ``{param}`` 同样是「需要被替换的参数」
    for (const param of queryParams) {
        const placeholder = unwrapPlaceholder(param.value)
        if (placeholder && !pathParams.includes(placeholder)) pathParams.push(placeholder)
    }

    return { urlTemplate, pathParams, queryParams }
}

/** 解析 curl 命令。 */
const parseCurl = (text: string): ParsedRequest | ParseFailure => {
    const warnings: string[] = []
    const tokens = tokenizeShellLike(text)
    const rest = tokens[0] && tokens[0].toLowerCase().startsWith('curl') ? tokens.slice(1) : tokens

    let method = ''
    let url = ''
    const headers: Record<string, string> = {}
    let bodyText: string | null = null

    for (let i = 0; i < rest.length; i += 1) {
        const token = rest[i]
        const next = rest[i + 1]

        if (token === '-X' || token === '--request') {
            if (next) { method = next.toUpperCase(); i += 1 }
            continue
        }
        if (token === '-H' || token === '--header') {
            if (next !== undefined) {
                const index = next.indexOf(':')
                if (index > 0) {
                    const name = next.slice(0, index).trim()
                    const value = next.slice(index + 1).trim()
                    if (name) headers[name] = value
                } else {
                    warnings.push(`没认出这个请求头：${next}`)
                }
                i += 1
            }
            continue
        }
        if (BODY_OPTION_FLAGS.includes(token)) {
            if (next !== undefined) { bodyText = next; i += 1 }
            continue
        }
        if (token === '--url') {
            if (next !== undefined) { url = next; i += 1 }
            continue
        }
        if (token.startsWith('-')) {
            // 其他开关（-G / --compressed / -u …）本次不支持，明确告知而不是默默忽略
            warnings.push(`暂不支持的 curl 选项已忽略：${token}`)
            continue
        }
        if (!url) url = token
    }

    if (!url) return { error: '这条 curl 里没找到请求地址。请确认命令里带了完整的 URL' }

    const parsedUrl = parseUrl(url, warnings)
    if ('error' in parsedUrl) return parsedUrl

    const bodyParams: ParsedBodyParam[] = []
    if (bodyText !== null && bodyText !== '') {
        let parsedBody: unknown
        try {
            parsedBody = JSON.parse(bodyText)
        } catch {
            warnings.push('请求体不是 JSON，没能自动识别出参数——请在第 3 步手动补上')
            parsedBody = undefined
        }
        if (parsedBody && typeof parsedBody === 'object' && !Array.isArray(parsedBody)) {
            for (const [name, value] of Object.entries(parsedBody as Record<string, unknown>)) {
                bodyParams.push({ name, value: stringifyBodyValue(value), type: inferBodyType(value) })
            }
        } else if (parsedBody !== undefined) {
            warnings.push('请求体不是 JSON 对象，没能自动识别出参数——请在第 3 步手动补上')
        }
    }

    const finalMethod = method || (bodyParams.length > 0 ? 'POST' : 'GET')
    if (!isSupportedMethod(finalMethod)) {
        warnings.push(
            `平台运行时不支持 ${finalMethod} 方法，可用的只有 ${SUPPORTED_HTTP_METHODS.join(' / ')}`,
        )
    }

    return {
        method: finalMethod,
        urlTemplate: parsedUrl.urlTemplate,
        pathParams: parsedUrl.pathParams,
        queryParams: parsedUrl.queryParams,
        bodyParams,
        headers,
        warnings,
    }
}

/**
 * 解析用户粘贴的内容：curl 命令或一条完整 URL。
 * **任何情况下都不抛异常**——解析不了就返回 ``{ error }`` 由界面原地提示。
 */
export const parseRequestInput = (text: string): ParsedRequest | ParseFailure => {
    const raw = String(text || '').trim()
    if (!raw) return { error: '请先粘贴一条请求地址或 curl 命令' }

    const looksLikeCurl = /^\s*curl(\s|$)/i.test(raw)
    if (looksLikeCurl) return parseCurl(raw)

    const warnings: string[] = []
    const parsedUrl = parseUrl(raw, warnings)
    if ('error' in parsedUrl) return parsedUrl

    if (parsedUrl.queryParams.length === 0 && parsedUrl.pathParams.length === 0 && !parsedUrl.urlTemplate.includes('?')) {
        warnings.push('这条地址里没有任何参数，配出来的工具将不需要输入')
    }

    return {
        method: 'GET',
        urlTemplate: parsedUrl.urlTemplate,
        pathParams: parsedUrl.pathParams,
        queryParams: parsedUrl.queryParams,
        bodyParams: [],
        headers: {},
        warnings,
    }
}

export const isParsedRequest = (value: ParsedRequest | ParseFailure): value is ParsedRequest =>
    !('error' in value)
```

- [ ] **Step 4: 跑 Node 测试确认通过**

Run: `node --experimental-strip-types --no-warnings tests/frontend/tool_import_roundtrip.mjs`
Expected: 全部 ✓，最后一行 `工具导入往返测试全部通过（10 项）`

- [ ] **Step 5: 写 pytest 包装**

创建 `tests/frontend/test_api_tool_import_contract.py`：

```python
"""API 工具「粘贴请求」解析契约。

静态断言只能证明代码长什么样，证明不了「解析会不会把用户的请求读错」——
所以核心行为用 ``node`` 真跑一遍纯函数（照 ``tool_schema_roundtrip.mjs`` 的先例）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

IMPORT_UTIL = Path("frontend/src/utils/toolImport.ts")
ROUNDTRIP = Path("tests/frontend/tool_import_roundtrip.mjs")


def _source(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"缺少文件：{path}")
    return path.read_text(encoding="utf-8")


def test_import_util_never_throws():
    """解析失败必须是返回值，不是异常——界面上要原地提示并保留用户原文。"""
    source = _source(IMPORT_UTIL)
    assert "export const parseRequestInput" in source
    assert "{ error: string }" in source or "ParseFailure" in source


def test_import_util_covers_url_and_curl():
    source = _source(IMPORT_UTIL)
    assert "parseCurl" in source
    assert "tokenizeShellLike" in source
    for flag in ("-X", "--request", "-H", "--header", "--url"):
        assert flag in source, f"curl 解析缺少 {flag}"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数往返测试")
def test_import_roundtrip_behaviour_via_node():
    assert ROUNDTRIP.exists(), "往返测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(ROUNDTRIP)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工具导入往返测试全部通过" in proc.stdout
```

- [ ] **Step 6: 跑 pytest 确认通过**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_import_contract.py -q`
Expected: 3 passed

---

## Task 3: `toolDraft.ts`——落位判定与必填建议

**Files:**
- Create: `frontend/src/utils/toolDraft.ts`
- Create: `tests/frontend/tool_draft_roundtrip.mjs`
- Modify: `tests/frontend/test_api_tool_import_contract.py`（追加一条 Node 往返用例）

- [ ] **Step 1: 写失败测试（Node 真跑）**

创建 `tests/frontend/tool_draft_roundtrip.mjs`：

```js
import assert from 'node:assert/strict'
import { parseRequestInput } from '../../frontend/src/utils/toolImport.ts'
import { buildDraftParams, buildDraftSchema, resolveParamLocation } from '../../frontend/src/utils/toolDraft.ts'
import { parseToolSchema } from '../../frontend/src/utils/toolSchema.ts'

let passed = 0
const check = (label, fn) => { fn(); passed += 1; console.log('  ✓ ' + label) }

check('落位：地址占位符永远算 path，与请求方法无关', () => {
  assert.equal(resolveParamLocation('GET', 'https://x/{id}', 'id'), 'path')
  assert.equal(resolveParamLocation('POST', 'https://x/{id}', 'id'), 'path')
})

check('落位：GET 的其余参数走 query，非 GET 走 body', () => {
  assert.equal(resolveParamLocation('GET', 'https://x/a', 'city'), 'query')
  assert.equal(resolveParamLocation('POST', 'https://x/a', 'amount'), 'body')
  assert.equal(resolveParamLocation('PATCH', 'https://x/a', 'amount'), 'body')
})

check('必填：占位符参数必填，且不带默认值', () => {
  const parsed = parseRequestInput('https://api.test/v1/orders/{order_id}/refund')
  const params = buildDraftParams(parsed)
  assert.equal(params.length, 1)
  assert.equal(params[0].name, 'order_id')
  assert.equal(params[0].required, true)
  assert.equal(params[0].defaultValue, '')
})

check('必填：URL 里带了值的参数自动变可选 + 默认值', () => {
  const parsed = parseRequestInput('https://api.test/w?city=北京&days=3')
  const params = buildDraftParams(parsed)
  const city = params.find((p) => p.name === 'city')
  assert.equal(city.required, false)
  assert.equal(city.defaultValue, '北京')
  assert.equal(city.type, 'string')
  const days = params.find((p) => p.name === 'days')
  assert.equal(days.required, false)
  assert.equal(days.defaultValue, '3')
})

check('必填：query 里无值的参数判为必填', () => {
  const parsed = parseRequestInput('https://api.test/w?city=')
  const params = buildDraftParams(parsed)
  assert.equal(params[0].required, true)
})

check('类型：从请求体的 JSON 值推断，而不是一律 string', () => {
  const parsed = parseRequestInput(`curl -X POST https://api.test/o -d '{"amount": 100, "ratio": 1.5, "ok": true, "tags": ["a"], "meta": {"x": 1}}'`)
  const params = buildDraftParams(parsed)
  const typeOf = (name) => params.find((p) => p.name === name).type
  assert.equal(typeOf('amount'), 'integer')
  assert.equal(typeOf('ratio'), 'number')
  assert.equal(typeOf('ok'), 'boolean')
  assert.equal(typeOf('tags'), 'array')
  assert.equal(typeOf('meta'), 'object')
})

check('body 里带了值的参数同样变可选 + 默认值', () => {
  const parsed = parseRequestInput(`curl -X POST https://api.test/o -d '{"amount": 100}'`)
  const params = buildDraftParams(parsed)
  assert.equal(params[0].required, false)
  assert.equal(params[0].defaultValue, '100')
})

check('生成结果能被 toolSchema 往返而不变形（扁平形态）', () => {
  const parsed = parseRequestInput(`curl -X POST 'https://api.test/v1/orders/{order_id}/refund' -d '{"amount": 100}'`)
  const schema = buildDraftSchema(buildDraftParams(parsed))
  const parsedSchema = parseToolSchema(schema)
  assert.equal(parsedSchema.shape, 'flat')
  assert.deepEqual(parsedSchema.params.map((p) => [p.name, p.type, p.required]), [
    ['order_id', 'string', true],
    ['amount', 'integer', false],
  ])
  // 非必填必须落成内联 required:false，否则运行时按缺省必填处理
  assert.equal(JSON.parse(schema).amount.required, false)
  assert.equal(JSON.parse(schema).amount.default, 100)
})

check('参数名为空白的行不会写进 schema', () => {
  const schema = buildDraftSchema(
    buildDraftParams({ method: 'GET', urlTemplate: 'https://x/a', pathParams: [], queryParams: [], bodyParams: [], headers: {}, warnings: [] })
      .concat([{ name: '   ', type: 'string', required: true, description: '', defaultValue: '', location: 'query', reason: '' }]),
  )
  assert.deepEqual(JSON.parse(schema), {})
})

console.log(`\n工具草稿往返测试全部通过（${passed} 项）`)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --experimental-strip-types --no-warnings tests/frontend/tool_draft_roundtrip.mjs`
Expected: FAIL，`Cannot find module .../toolDraft.ts`

- [ ] **Step 3: 实现 toolDraft.ts**

创建 `frontend/src/utils/toolDraft.ts`：

```ts
/**
 * 由「解析出来的请求」生成参数清单：判落位、判必填、推类型。
 *
 * 落位规则必须与运行时 ``GenericApiToolFactory._execute_request`` 完全同源，
 * 否则向导会给出「试跑能过、运行时调不通」的假结论：
 *   1. 地址里出现 ``{name}`` → 路径替换（**与方法无关**，运行时最先做这一步）；
 *   2. 剩下的参数：GET 走 query，POST/PUT/PATCH/DELETE 走 JSON body。
 *
 * 必填规则（对齐运行时的「缺省必填」语义）：
 *   * 占位符参数 ``{city}`` → 必填，无默认值；
 *   * 请求里带了**非空且非占位符**的值 → 可选 + 默认值 = 该值（用户少填就少错）；
 *   * 其余（``?city=`` 这种空值）→ 必填。
 *
 * 序列化一律复用 ``toolSchema.ts``：新建时用**扁平形态**（与表单 placeholder 同款），
 * 非必填由它落成内联 ``required: false``——标准形态下的 ``required: []`` 表达不了
 * 「全部可选」（空数组是 falsy，运行时掉回默认必填分支）。
 */
import type { ParsedRequest } from './toolImport'
import { serializeToolSchema } from './toolSchema'

export type ParamLocation = 'path' | 'query' | 'body'

export type DraftParam = {
    name: string
    type: string
    required: boolean
    description: string
    defaultValue: string
    location: ParamLocation
    /** 给用户看的「为什么这么判」，第 3 步逐行展示 */
    reason: string
}

const isPlaceholderValue = (value: string): boolean => /^\{[^{}]+\}$/.test(value)

/** 判定单个参数在请求里的落位（与运行时同源）。 */
export const resolveParamLocation = (
    method: string,
    urlTemplate: string,
    name: string,
): ParamLocation => {
    if (!name) return 'query'
    if (urlTemplate.includes(`{${name}}`)) return 'path'
    return String(method || 'GET').toUpperCase() === 'GET' ? 'query' : 'body'
}

const locationLabel = (location: ParamLocation): string =>
    location === 'path' ? '路径参数' : location === 'query' ? '查询参数' : '请求体'

/** 由解析结果生成参数清单（顺序保持原请求里的出现次序）。 */
export const buildDraftParams = (parsed: ParsedRequest): DraftParam[] => {
    const method = String(parsed.method || 'GET').toUpperCase()
    const urlTemplate = parsed.urlTemplate || ''
    const params: DraftParam[] = []
    const seen = new Set<string>()

    const push = (
        name: string,
        rawValue: string,
        type: string,
        location: ParamLocation,
    ) => {
        if (!name || seen.has(name)) return
        seen.add(name)
        const required = rawValue === '' || isPlaceholderValue(rawValue)
        params.push({
            name,
            type,
            required,
            description: '',
            defaultValue: required ? '' : rawValue,
            location,
            reason: required
                ? `${locationLabel(location)}${isPlaceholderValue(rawValue) ? '，地址里是 { } 占位符，需要被替换' : '，原始请求里没有给值'} → 判为必填`
                : `${locationLabel(location)}，原始请求里带了值 ${rawValue} → 已设为可选并作为默认值`,
        })
    }

    // 1. 地址里的占位符（path 与 query 两处都算），一律路径替换语义
    for (const name of parsed.pathParams || []) {
        push(name, `{${name}}`, 'string', 'path')
    }

    // 2. 其余 query 参数
    for (const item of parsed.queryParams || []) {
        if (isPlaceholderValue(item.value)) continue // 已在上面按占位符处理
        push(item.name, item.value, 'string', resolveParamLocation(method, urlTemplate, item.name))
    }

    // 3. 请求体参数
    for (const item of parsed.bodyParams || []) {
        if (isPlaceholderValue(item.value)) {
            push(item.name, item.value, item.type || 'string', 'body')
            continue
        }
        push(item.name, item.value, item.type || 'string', 'body')
    }

    return params
}

/** 参数清单 → ``parameter_schema`` 文本（扁平形态，非必填落成内联 required:false）。 */
export const buildDraftSchema = (params: DraftParam[]): string => serializeToolSchema({
    shape: 'flat',
    hadRequiredList: false,
    rest: {},
    params: params.map((param) => ({
        name: param.name,
        type: param.type,
        required: param.required,
        description: param.description,
        defaultValue: param.defaultValue,
        extra: {},
    })),
})

/** 参数清单 → 落位映射，供 ``ToolParamEditor`` 在向导模式里显示标签。 */
export const buildLocationMap = (params: DraftParam[]): Record<string, ParamLocation> => {
    const map: Record<string, ParamLocation> = {}
    for (const param of params) {
        if (param.name) map[param.name] = param.location
    }
    return map
}
```

- [ ] **Step 4: 跑 Node 测试确认通过**

Run: `node --experimental-strip-types --no-warnings tests/frontend/tool_draft_roundtrip.mjs`
Expected: 全部 ✓，最后一行 `工具草稿往返测试全部通过（8 项）`

- [ ] **Step 5: 把这条 Node 往返挂进 pytest**

在 `tests/frontend/test_api_tool_import_contract.py` 末尾追加：

```python
DRAFT_ROUNDTRIP = Path("tests/frontend/tool_draft_roundtrip.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数往返测试")
def test_draft_roundtrip_behaviour_via_node():
    assert DRAFT_ROUNDTRIP.exists(), "往返测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(DRAFT_ROUNDTRIP)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工具草稿往返测试全部通过" in proc.stdout
```

- [ ] **Step 6: 跑 pytest 确认通过**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_import_contract.py -q`
Expected: 4 passed

---

## Task 4: 后端抽出 `_perform_request` 内核（运行时输出逐字节不变）

**Files:**
- Modify: `app/services/ai/tools/generic_api.py:124-183`
- Create: `tests/api/portal/test_generic_api_runtime_contract.py`

- [ ] **Step 1: 写失败测试（先钉住现状）**

创建 `tests/api/portal/test_generic_api_runtime_contract.py`：

```python
"""``GenericApiToolFactory._execute_request`` 的对外输出契约。

为什么需要它：为了让「保存前试跑」与运行时**走同一段请求代码**（否则会出现
「试跑通过、运行时失败」的口径分裂），本次把请求内核抽成了 ``_perform_request``。
抽取是纯重构，**运行时对外的返回值必须逐字节不变**——这些用例就是那道闸门。

覆盖五种情形：2xx JSON、2xx 文本、204 空体、非 2xx、连接失败。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory


def _tool(**overrides) -> SysApiTool:
    payload = {
        "id": "t1",
        "name": "demo_tool",
        "method": "GET",
        "url_template": "https://api.test/v1/items",
        "headers": "{}",
        "parameter_schema": "{}",
        "is_active": True,
    }
    payload.update(overrides)
    return SysApiTool(**payload)


def _response(status_code: int, *, json_body=None, text: str | None = None, content: bytes | None = None):
    response = httpx.Response(
        status_code=status_code,
        json=json_body,
        text=None if json_body is not None else text,
        content=content,
        request=httpx.Request("GET", "https://api.test/v1/items"),
    )
    return response


@pytest.mark.asyncio
async def test_2xx_json_is_dumped_without_ascii_escape():
    response = _response(200, json_body={"城市": "北京"})
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(response, 12, "https://api.test/v1/items")),
    ):
        result = await GenericApiToolFactory._execute_request(_tool(), {})
    assert result == json.dumps({"城市": "北京"}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_2xx_plain_text_is_returned_as_is():
    response = _response(200, text="hello")
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(response, 12, "https://api.test/v1/items")),
    ):
        result = await GenericApiToolFactory._execute_request(_tool(), {})
    assert result == "hello"


@pytest.mark.asyncio
async def test_204_returns_success_envelope():
    response = _response(204, content=b"")
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(response, 12, "https://api.test/v1/items")),
    ):
        result = await GenericApiToolFactory._execute_request(_tool(), {})
    assert result == {"success": True, "content": ""}


@pytest.mark.asyncio
async def test_non_2xx_becomes_execution_error_with_status_and_truncated_body():
    """非 2xx 的文案格式是既有契约（前端与日志都按它认），不能变。"""
    response = _response(404, text="nope")
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(
            side_effect=RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
        ),
    ):
        result = await GenericApiToolFactory._execute_request(_tool(), {})
    assert result == "[Execution Error] HTTP 404: nope"


@pytest.mark.asyncio
async def test_connection_failure_becomes_execution_error():
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=httpx.ConnectError("boom")),
    ):
        result = await GenericApiToolFactory._execute_request(_tool(), {})
    assert result == "[Execution Error] boom"


@pytest.mark.asyncio
async def test_unsupported_method_keeps_original_wording():
    response = _response(200, text="x")
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=RuntimeError("Unsupported method: HEAD")),
    ):
        result = await GenericApiToolFactory._execute_request(_tool(method="HEAD"), {})
    assert result == "[Execution Error] Unsupported method: HEAD"


@pytest.mark.asyncio
async def test_perform_request_is_the_real_request_path():
    """内核必须真的发请求，而不是被绕过——否则上面几条会变成自证。"""
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    with patch("app.services.ai.tools.generic_api.httpx.AsyncClient", FakeClient):
        response, elapsed, url = await GenericApiToolFactory._perform_request(
            _tool(url_template="https://api.test/v1/items/{id}"), {"id": "7", "q": "x"},
        )
    assert response.status_code == 200
    assert url == "https://api.test/v1/items/7"
    # 路径参数从 query 里剔除，其余作为 query 发出
    assert captured["params"] == {"q": "x"}
    assert isinstance(elapsed, int)
    assert captured["kwargs"]["trust_env"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/api/portal/test_generic_api_runtime_contract.py -q`
Expected: FAIL——`_perform_request` 尚不存在（AttributeError）。

- [ ] **Step 3: 重构 generic_api.py**

把 `app/services/ai/tools/generic_api.py` 的 `_execute_request`（第 124-183 行）整段替换为下面两个方法：

```python
    @staticmethod
    async def _perform_request(
        config: SysApiTool,
        params: Dict[str, Any],
        *,
        raise_on_http_error: bool = True,
    ) -> tuple[Any, int, str]:
        """发一次请求的公共内核：路径替换 + 参数落位 + headers + 超时。

        抽出来的唯一目的是让「保存前试跑」与运行时**走同一段代码**，
        避免出现「试跑通过、运行时失败」的口径分裂。

        返回 ``(response, elapsed_ms, final_url)``。
        ``raise_on_http_error=True``（运行时默认）时非 2xx 抛 ``RuntimeError``，
        文案与历史行为逐字节一致；试跑传 ``False`` 以取得原始 status 与 body。
        连接层异常一律原样抛出，由调用方决定怎么呈现。
        """
        url = config.url_template

        # 1. Path Substitution
        path_params = {}
        for key, value in params.items():
            placeholder = f"{{{key}}}"
            if placeholder in url:
                url = url.replace(placeholder, str(value))
                path_params[key] = value

        # Filter out path params from remaining params
        remaining_params = {k: v for k, v in params.items() if k not in path_params}

        # 2. Headers
        headers = {}
        if config.headers:
            h_config = config.headers
            if isinstance(h_config, str):
                try:
                    h_config = json.loads(h_config)
                except Exception:
                    h_config = {}
            if isinstance(h_config, dict):
                headers.update(h_config)

        # 3. Request
        method = config.method.upper()

        logger.info(f"[GenericTool] Executing {config.name} ({method} {url}) with params: {remaining_params}")

        started = time.monotonic()
        # We disable trust_env=True to avoid SOCKS proxy issues if dependency is missing
        # If explicit proxy is needed, it should be configured in settings or handled via specific client config.
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            if method == "GET":
                response = await client.get(url, params=remaining_params, headers=headers)
            elif method in ["POST", "PUT", "PATCH", "DELETE"]:
                # Default to JSON body for non-GET
                response = await client.request(method, url, json=remaining_params, headers=headers)
            else:
                raise UnsupportedMethodError(method)

        elapsed_ms = int((time.monotonic() - started) * 1000)

        if raise_on_http_error and not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:500]}"
            )

        return response, elapsed_ms, url

    @staticmethod
    async def _execute_request(config: SysApiTool, params: Dict[str, Any]) -> Any:
        """
        Executes the HTTP request.
        """
        try:
            response, _elapsed_ms, _url = await GenericApiToolFactory._perform_request(
                config, params
            )

            if response.status_code == 204 or not response.content:
                return {"success": True, "content": ""}

            # Try to format JSON result
            try:
                resp_json = response.json()
                return json.dumps(resp_json, ensure_ascii=False)
            except Exception:
                return response.text

        except UnsupportedMethodError as e:
            # 历史文案：``[Error] Unsupported method: XXX``
            return f"[Error] {e}"
        except Exception as e:
            logger.error(f"[GenericTool] Execution failed: {e}")
            return f"[Execution Error] {str(e)}"
```

在文件顶部的 import 区加 `import time`，并在 `GenericApiToolFactory` 类定义**之前**加异常类：

```python
class UnsupportedMethodError(Exception):
    """运行时遇到不支持的方法时抛出。

    单独成一个类型（而不是直接 return 字符串）是为了让 ``_perform_request``
    的返回类型保持「一定是 response」，同时让 ``_execute_request`` 能逐字节
    复现历史文案 ``[Error] Unsupported method: XXX``，试跑端点也能把它归成
    ``unsupported_method`` 而不是笼统的连接失败。
    """

    def __init__(self, method: str):
        self.method = method
        super().__init__(f"Unsupported method: {method}")
```

- [ ] **Step 4: 跑新契约测试确认通过**

Run: `.venv/bin/python -m pytest tests/api/portal/test_generic_api_runtime_contract.py -q`
Expected: 7 passed

- [ ] **Step 5: 跑既有工具链路回归**

Run: `.venv/bin/python -m pytest tests/api/portal tests/ai/tools -q 2>&1 | tail -3`
Expected: 全绿（若 `tests/ai/tools` 不存在，只跑 `tests/api/portal`）

---

## Task 5: 后端 `POST /api/portal/tools/preview`

**Files:**
- Modify: `app/schemas/tool.py`（追加两个模型）
- Modify: `app/api/portal/endpoints/tools.py`（追加端点）
- Create: `tests/api/portal/test_tool_preview.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/api/portal/test_tool_preview.py`：

```python
"""``POST /api/portal/tools/preview``：保存前试跑。

三条不可退让的约束：
1. **只读**——不写 ``sys_api_tools``，请求体里的 ``params`` 是测试值、绝不落库；
2. **同权**——权限与创建/更新工具一致（``element:system:config_save``），不放宽也不新增；
3. **同口径**——走 ``GenericApiToolFactory._perform_request``，与运行时同一段代码。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory, UnsupportedMethodError

PREVIEW_URL = "/api/portal/tools/preview"


def _payload(**overrides):
    payload = {
        "method": "GET",
        "url_template": "https://api.test/v1/items",
        "headers": {"Authorization": "Bearer sk-x"},
        "parameter_schema": {"id": {"type": "string"}},
        "params": {"id": "7"},
    }
    payload.update(overrides)
    return payload


def _response(status_code=200, *, json_body=None, text=None, content=None):
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        text=None if json_body is not None else text,
        content=content,
        request=httpx.Request("GET", "https://api.test/v1/items"),
    )


@pytest.mark.asyncio
async def test_preview_requires_system_config_save(client, auth_headers_no_permission):
    response = await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers_no_permission)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_preview_does_not_touch_tool_table(client, auth_headers, db_session):
    before = (await db_session.execute(__import__("sqlalchemy").select(SysApiTool))).scalars().all()
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(200, json_body={"ok": True}), 8, "https://api.test/v1/items")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status_code"] == 200

    after = (await db_session.execute(__import__("sqlalchemy").select(SysApiTool))).scalars().all()
    assert len(after) == len(before), "试跑不得写入工具表"
    assert all("7" not in str(tool.parameter_schema) for tool in after), "测试值不得落库"


@pytest.mark.asyncio
async def test_preview_refuses_unfilled_placeholders_without_sending(client, auth_headers):
    with patch.object(GenericApiToolFactory, "_perform_request", new=AsyncMock()) as mocked:
        response = await client.post(
            PREVIEW_URL,
            json=_payload(url_template="https://api.test/v1/orders/{order_id}"),
            headers=auth_headers,
        )
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "missing_param"
    assert "order_id" in body["message"]
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_preview_classifies_http_error(client, auth_headers):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(404, text="not found"), 9, "https://api.test/v1/items")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers)
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "http_error"
    assert body["status_code"] == 404
    assert "404" in body["message"]


@pytest.mark.asyncio
async def test_preview_classifies_timeout(client, auth_headers):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=httpx.TimeoutException("timed out")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers)
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "timeout"


@pytest.mark.asyncio
async def test_preview_classifies_connection_failure(client, auth_headers):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=httpx.ConnectError("dns boom")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers)
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "connection"


@pytest.mark.asyncio
async def test_preview_classifies_unsupported_method(client, auth_headers):
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(side_effect=UnsupportedMethodError("HEAD")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(method="HEAD"), headers=auth_headers)
    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "unsupported_method"


@pytest.mark.asyncio
async def test_preview_truncates_long_body(client, auth_headers):
    long_text = "x" * 5000
    with patch.object(
        GenericApiToolFactory, "_perform_request",
        new=AsyncMock(return_value=(_response(200, text=long_text), 10, "https://api.test/v1/items")),
    ):
        response = await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers)
    body = response.json()
    assert body["ok"] is True
    assert body["body_truncated"] is True
    assert len(body["body"]) == 2000


@pytest.mark.asyncio
async def test_preview_passes_draft_without_persisting(client, auth_headers):
    """内核收到的必须是内存草稿，且 headers/schema 已序列化成字符串（与运行时同形）。"""
    captured = {}

    async def fake_perform(config, params, **kwargs):
        captured["config"] = config
        captured["params"] = params
        captured["kwargs"] = kwargs
        return _response(200, json_body={"ok": True}), 5, "https://api.test/v1/items"

    with patch.object(GenericApiToolFactory, "_perform_request", new=fake_perform):
        await client.post(PREVIEW_URL, json=_payload(), headers=auth_headers)

    config = captured["config"]
    assert isinstance(config.headers, str)
    assert isinstance(config.parameter_schema, str)
    assert captured["params"] == {"id": "7"}
    # 试跑必须拿到非 2xx 的原始响应用于分类展示，而不是让它抛异常
    assert captured["kwargs"].get("raise_on_http_error") is False
```

> **说明**：`client` / `auth_headers` / `auth_headers_no_permission` / `db_session` 这几个 fixture 在
> `tests/conftest.py` 与 `tests/api/conftest.py` 里已有同类实现。**实现前必须先打开这两个 conftest
> 核对实际名字**；若权限串不同（例如需要 `require_permission("element", "element:system:config_save")` 的
> 无权限 header 另有其名），按仓库里已有的 mock 权限写法对齐（可参考 `tests/api/portal/test_management.py`
> 里对 403 的既有断言方式），**不要新造 fixture**。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/api/portal/test_tool_preview.py -q`
Expected: FAIL——路由 404 或 fixture 名不符；先把 fixture 名对齐（见上面的说明），确保失败原因是「端点不存在」。

- [ ] **Step 3: 加 schema**

在 `app/schemas/tool.py` 末尾追加：

```python
class SysApiToolPreviewRequest(BaseModel):
    """保存前试跑：提交**草稿**（不落库），由服务端代发一次请求。"""

    method: str = Field("GET", description="HTTP Method")
    url_template: str = Field(..., min_length=1, description="目标 API URL 模板")
    headers: Optional[Dict[str, str]] = Field(default_factory=dict)
    parameter_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="试跑测试值：只用于这一次请求，绝不写入 sys_api_tools",
    )


class SysApiToolPreviewResponse(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    elapsed_ms: int = 0
    body: str = ""
    body_truncated: bool = False
    error_kind: Optional[str] = Field(
        None,
        description="http_error / connection / timeout / invalid_url / unsupported_method / missing_param",
    )
    message: str = ""
```

- [ ] **Step 4: 加端点**

在 `app/api/portal/endpoints/tools.py` 的 import 区补：

```python
import re
import time

import httpx

from app.schemas.tool import (
    SysApiToolPreviewRequest,
    SysApiToolPreviewResponse,
)
from app.services.ai.tools.generic_api import GenericApiToolFactory, UnsupportedMethodError
```

（`SysApiToolPreviewRequest/Response` 请并入该文件已有的 `from app.schemas.tool import ...` 语句，不要重复 import 行。）

在 `delete_tool` 之前追加：

```python
#: 试跑响应体回显上限：足够看清结构，又不会被超大响应拖垮界面与审计日志。
PREVIEW_BODY_LIMIT = 2000

#: URL 模板里未替换的参数占位符。
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@router.post("/preview", response_model=SysApiToolPreviewResponse)
async def preview_tool(
    payload: SysApiToolPreviewRequest,
    user: Dict = Depends(SYSTEM_CONFIG_SAVE_PERMISSION),
):
    """保存前试跑一次（不落库、不新增权限）。

    **走的是与运行时完全相同的请求内核**（``GenericApiToolFactory._perform_request``）：
    另写一套请求逻辑必然导致「试跑能过、运行时调不通」的口径分裂。

    对目标地址**不额外设限**——能创建工具的人本来就能让智能体请求同一地址，
    在试跑上加限制只会误伤内网自建 API，并不提高安全水位。私网地址由前端醒目
    提示；每次试跑都会经审计中间件留痕（请求体里的凭据由 mask_sensitive_data 脱敏）。
    """
    leftover = _PLACEHOLDER_RE.findall(payload.url_template)
    if leftover:
        names = "、".join(f"{{{name}}}" for name in leftover)
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="missing_param",
            message=f"地址里还有没填的参数：{names}。请先在参数里填入测试值再试跑",
        )

    method = (payload.method or "GET").strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="unsupported_method",
            message=f"平台运行时不支持 {method} 方法",
        )

    # 内存草稿：与运行时拿到的 SysApiTool 同形（headers / schema 都是 JSON 字符串）
    draft = SysApiTool(
        id="__preview__",
        name="__preview__",
        method=method,
        url_template=payload.url_template,
        headers=json.dumps(payload.headers or {}, ensure_ascii=False),
        parameter_schema=json.dumps(payload.parameter_schema or {}, ensure_ascii=False),
        is_active=False,
    )

    try:
        response, elapsed_ms, _url = await GenericApiToolFactory._perform_request(
            draft,
            dict(payload.params or {}),
            # 非 2xx 也要拿到原始响应，用于分类展示「接口通了但拒绝」而不是笼统失败
            raise_on_http_error=False,
        )
    except UnsupportedMethodError as exc:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="unsupported_method", message=str(exc),
        )
    except httpx.TimeoutException:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="timeout",
            message="请求超时（30 秒）。目标接口没有在时限内响应，请确认地址是否可达",
        )
    except httpx.InvalidURL:
        return SysApiToolPreviewResponse(
            ok=False, error_kind="invalid_url", message="地址格式不合法，无法发起请求",
        )
    except httpx.HTTPError as exc:
        return SysApiToolPreviewResponse(
            ok=False,
            error_kind="connection",
            message=f"没能连上目标接口（{type(exc).__name__}）：{exc}",
        )
    except Exception as exc:  # noqa: BLE001 —— 试跑必须给出原因而不是 500
        logger.warning("[ToolPreview] unexpected failure: %s", exc)
        return SysApiToolPreviewResponse(
            ok=False, error_kind="connection", message=f"试跑失败：{exc}",
        )

    raw_body = response.text or ""
    truncated = len(raw_body) > PREVIEW_BODY_LIMIT
    body_text = raw_body[:PREVIEW_BODY_LIMIT]

    if 200 <= response.status_code < 300:
        return SysApiToolPreviewResponse(
            ok=True,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
            body=body_text,
            body_truncated=truncated,
            message=f"HTTP {response.status_code}",
        )

    return SysApiToolPreviewResponse(
        ok=False,
        status_code=response.status_code,
        elapsed_ms=elapsed_ms,
        body=body_text,
        body_truncated=truncated,
        error_kind="http_error",
        message=f"接口返回 HTTP {response.status_code}——地址是通的，但目标接口拒绝了这次请求",
    )
```

并在该文件顶部确认已有 `logger = logging.getLogger(__name__)`；若没有，加上 `import logging` 与 `logger = logging.getLogger(__name__)`。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/api/portal/test_tool_preview.py -q`
Expected: 9 passed

- [ ] **Step 6: 跑后端回归**

Run: `.venv/bin/python -m pytest tests/api/portal -q 2>&1 | tail -3`
Expected: 与 Task 0 基线相比只多新增用例数，无失败。

---

## Task 6: 前端 API 封装 + 试跑面板

**Files:**
- Modify: `frontend/src/api/tool.ts`
- Create: `frontend/src/components/system/ApiToolTester.vue`

- [ ] **Step 1: 加 API 封装**

在 `frontend/src/api/tool.ts` 的接口区加：

```ts
export interface SysApiToolPreviewPayload {
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  /** 试跑测试值：只用于这一次请求，绝不落库 */
  params: Record<string, any>
}

export interface SysApiToolPreviewResult {
  ok: boolean
  status_code?: number | null
  elapsed_ms: number
  body: string
  body_truncated: boolean
  error_kind?: 'http_error' | 'connection' | 'timeout' | 'invalid_url' | 'unsupported_method' | 'missing_param' | null
  message: string
}
```

在 `toolApi` 对象里加：

```ts
  /** 保存前试跑一次：草稿不落库，服务端走与运行时相同的请求内核 */
  preview: (data: SysApiToolPreviewPayload) => {
    return axios.post<SysApiToolPreviewResult>('/api/portal/tools/preview', data)
  },
```

- [ ] **Step 2: 实现 ApiToolTester.vue**

创建 `frontend/src/components/system/ApiToolTester.vue`。交互范式参照 `McpToolTester.vue`（按 schema 生成输入 → 执行 → 展示结果），但**不引入 MCP 特有逻辑**（用户断言、rawJsonArgs 等）。

要点（模板与脚本都要满足，向导契约测试会断言）：

1. 顶部一句说明：**测试值不会保存到工具配置里**；
2. 按 `parameter_schema` 渲染输入框：
   - 读 `parseToolSchema(schemaStr)` 得到参数清单；
   - 只有 `required` 的参数显示红色 `*`；
   - 有默认值的预填该默认值（用 `param.defaultValue`）；
   - 日期/布尔等不做特殊控件，统一文本输入（YAGNI）；
3. 「试跑一次」按钮：`loading` 时禁用并显示「试跑中...」；
4. 私网/回环地址醒目提示（**不阻止**）；
5. 结果区：
   - 成功：绿头 `200 OK` + 耗时 + `POST https://...`，响应体在深色 `pre` 里；
   - `http_error`：琥珀/红头，明确写「地址是通的，但接口拒绝了这次请求」；
   - `connection` / `timeout`：红头，明确写「根本没连上目标接口」——与上一条的排查方向不同；
   - `missing_param`：不调接口，直接就地提示缺哪些参数；
   - `body_truncated` 为真时补一句「响应过大，仅显示前 2000 字符」；
6. 底部三条机制说明（试跑由平台服务器发起、走与运行时相同的请求逻辑；测试值不落库；每次试跑写审计日志，私网地址会提示但不阻止）。

脚本骨架：

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { toolApi, type SysApiToolPreviewResult } from '../../api/tool'
import { parseToolSchema } from '../../utils/toolSchema'

const props = defineProps<{
  method: string
  urlTemplate: string
  headersStr: string
  schemaStr: string
}>()

const values = ref<Record<string, string>>({})
const loading = ref(false)
const result = ref<SysApiToolPreviewResult | null>(null)
const failure = ref<string>('')

const params = computed(() => parseToolSchema(props.schemaStr).params)

/** 私网 / 回环 / 链路本地：不阻止试跑，但要让使用者知道这次请求打向哪里。 */
const isPrivateTarget = computed(() => {
  const host = (props.urlTemplate.match(/^[a-zA-Z][a-zA-Z0-9+.\-]*:\/\/([^/?#]+)/) || [])[1] || ''
  const bare = host.split('@').pop()!.split(':')[0].toLowerCase()
  if (!bare) return false
  if (bare === 'localhost' || bare.endsWith('.local') || bare.endsWith('.internal')) return true
  if (/^(10|127)\./.test(bare)) return true
  if (/^192\.168\./.test(bare)) return true
  if (/^172\.(1[6-9]|2\d|3[01])\./.test(bare)) return true
  if (/^169\.254\./.test(bare)) return true
  return false
})

/** 切换工具/重新解析参数时重置：旧结论留着会误导 */
watch(() => [props.method, props.urlTemplate, props.schemaStr], () => {
  const next: Record<string, string> = {}
  for (const param of params.value) {
    if (!param.required && param.defaultValue) next[param.name] = param.defaultValue
  }
  values.value = next
  result.value = null
  failure.value = ''
})

const missingRequired = computed(() => params.value
  .filter((param) => param.required && !String(values.value[param.name] ?? '').trim())
  .map((param) => param.name))

const runPreview = async () => {
  failure.value = ''
  result.value = null
  if (missingRequired.value.length > 0) {
    // 双保险：后端也会拦，但先在本地说清楚缺哪个
    failure.value = `请先填写必填参数：${missingRequired.value.join('、')}`
    return
  }
  let headers: Record<string, string> = {}
  try {
    headers = JSON.parse(props.headersStr || '{}')
  } catch {
    failure.value = 'Headers 不是合法 JSON，请先修正'
    return
  }
  let schema: Record<string, any> = {}
  try {
    schema = JSON.parse(props.schemaStr || '{}')
  } catch {
    failure.value = '参数定义不是合法 JSON，请先修正'
    return
  }
  loading.value = true
  try {
    const res = await toolApi.preview({
      method: props.method,
      url_template: props.urlTemplate,
      headers,
      parameter_schema: schema,
      params: { ...values.value },
    })
    result.value = res.data
  } catch (e: any) {
    failure.value = '试跑请求失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}
</script>
```

模板中**必须**出现这些锚点字符串（契约测试会断言）：
`试跑一次`、`测试值不会保存到工具配置里`、`地址是通的，但接口拒绝了这次请求`、`根本没连上目标接口`、`仅显示前`、`与运行时`、`审计日志`。

- [ ] **Step 3: 类型检查**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | tail -5`
Expected: 与 Task 0 基线相比零新增错误。

---

## Task 7: 抽出 `ToolParamEditor.vue` 并迁移契约锚点

**Files:**
- Create: `frontend/src/components/system/ToolParamEditor.vue`
- Modify: `frontend/src/components/system/ToolRegistry.vue:665-721`（参数编辑区整块移出）
- Modify: `tests/frontend/test_api_tool_schema_editor_contract.py`（约 8 条锚点迁移）

- [ ] **Step 1: 先跑既有契约，确认它是绿的**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_schema_editor_contract.py -q`
Expected: 18 passed

- [ ] **Step 2: 抽出组件**

创建 `frontend/src/components/system/ToolParamEditor.vue`：

- props：`modelValue: string`（schema 文本）、`mode?: 'full' | 'wizard'`（默认 `full`）、`locations?: Record<string, 'path' | 'query' | 'body'>`（默认 `{}`）
- emit：`update:modelValue`
- **逻辑一字不改地从 `ToolRegistry.vue` 搬过来**：`schemaEditorMode` / `visualParams` / `parsedSchema` / `visualParamSeed` / `nextVisualParamKey` / `loadVisualFromJson` / `buildSchemaJson` / `addVisualParam` / `removeVisualParam` / `switchSchemaMode` / `visualNestedCount`，以及模板里的 JSON textarea + 可视化行 + 增删按钮 + 嵌套标记
- 与 `modelValue` 的同步：
  - `watch(() => props.modelValue, (next) => { if (next !== buildSchemaJson() && schemaEditorMode.value === 'visual') loadVisualFromJson() })` —— JSON 模式下文本框直接 `:value="props.modelValue"` + `@input="$emit('update:modelValue', ...)"`，可视化模式下改动经 `buildSchemaJson()` 回写
  - 组件挂在 `v-model` 上（`ToolRegistry` 用 `v-model="toolForm.parameter_schema_str"`）
- `mode === 'wizard'` 时，每行参数名前多渲染一个落位标签：

```html
<span
  v-if="mode === 'wizard' && locations[p.name]"
  class="rounded px-1.5 py-0.5 text-[11px] font-medium"
  :class="{
    'bg-purple-50 text-purple-700': locations[p.name] === 'path',
    'bg-blue-50 text-blue-700': locations[p.name] === 'query',
    'bg-green-50 text-green-700': locations[p.name] === 'body',
  }"
>{{ locations[p.name] === 'path' ? '路径参数' : locations[p.name] === 'query' ? '查询参数' : '请求体' }}</span>
```

- **必须保留**的既有文案与类名（契约断言锚点，不能改）：
  - `>JSON</button>`、`>可视化</button>`
  - `v-if="schemaEditorMode === 'json'"`
  - `v-model="toolForm.parameter_schema_str"` → 在组件里改为 `:value="modelValue"` / `@input`，**契约里这一条要同步改写**（见 Step 3）
  - `visualNestedCount`、`JSON 模式`、`嵌套`
  - `生成 Schema` 相关的 placeholder 原文

- [ ] **Step 3: 迁移契约锚点**

改 `tests/frontend/test_api_tool_schema_editor_contract.py`：

1. 顶部新增：

```python
PARAM_EDITOR = Path("frontend/src/components/system/ToolParamEditor.vue")
```

2. 把锚在参数编辑区的断言从 `REGISTRY` 改为 `PARAM_EDITOR`（**逐条**，意图一律不变）：
   - `test_schema_editor_offers_both_modes` → `PARAM_EDITOR`
   - `test_textarea_only_renders_in_json_mode` → `PARAM_EDITOR`，并把断言 `'v-model="toolForm.parameter_schema_str"'` 改为 `'v-if="schemaEditorMode === \'json\'"'` 与 `'modelValue'`（组件现在收 `modelValue`）
   - `test_switching_to_visual_keeps_invalid_json_untouched` → `PARAM_EDITOR`
   - `test_switching_back_serializes_visual_rows` → `PARAM_EDITOR`
   - `test_visual_rows_expose_name_type_required_description_default` → `PARAM_EDITOR`
   - `test_visual_rows_can_be_added_and_removed` → `PARAM_EDITOR`
   - `test_default_value_only_applies_to_optional_params` → `PARAM_EDITOR`
   - `test_nested_params_are_flagged_to_the_user` → `PARAM_EDITOR`
   - `test_visual_editor_preserves_unknown_top_level_keys` 保留 `SCHEMA_UTIL` 断言，其中对 `REGISTRY` 的那半句改为 `PARAM_EDITOR`
   - `test_imported_tool_schema_is_loaded_by_shape_not_assumption` **留在 `REGISTRY`**（它锚的是 `openModal`，仍在 ToolRegistry）
   - 后端 4 条（`GENERIC_API`）**不动**
3. 新增一条「两个 tab 共用同一个参数编辑器」：

```python
def test_both_tabs_share_one_param_editor():
    """向导与表单必须共用同一个编辑器——两份参数 UI 必然逐渐漂移。"""
    registry = _source(REGISTRY)
    assert "ToolParamEditor" in registry, "表单/向导应通过 ToolParamEditor 渲染参数区"
    assert 'v-model="toolForm.parameter_schema_str"' in registry, (
        "编辑器必须仍然绑在 toolForm 上（单一数据源）"
    )
```

- [ ] **Step 4: 跑契约测试**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_schema_editor_contract.py -q`
Expected: 19 passed

- [ ] **Step 5: 类型检查 + 全量前端测试**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | tail -5`
Run: `.venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3`
Expected: 类型错误与基线归一化 diff 为空；前端测试全绿。

---

## Task 8: `ToolWizard.vue` 四步向导

**Files:**
- Create: `frontend/src/components/system/ToolWizard.vue`

- [ ] **Step 1: 实现向导壳**

创建 `frontend/src/components/system/ToolWizard.vue`：

- `v-model` 绑定整个 `toolForm`（类型与 `ToolRegistry` 里的 `toolForm` 一致）；
- 顶部步骤条：4 个可点击的步骤（`1 粘贴请求`、`2 起名与描述`、`3 确认参数`、`4 试跑一次`），`aria-current` 标出当前步；
- 步骤切换只改本地 `step` ref，**不校验**（允许自由回退）；
- **禁止出现 `wizardForm`** 这个标识符（契约测试会断言它不存在）——向导的一切都写回 `toolForm`。

四步内容：

**① 粘贴请求**
- textarea（本地 `pasteText` ref，不落 `toolForm`）
- 「解析这条请求」按钮 → `parseRequestInput`
- 失败：原地红字显示 `error`，**保留用户原文**
- 成功：写回 `toolForm.method` / `toolForm.url_template` / `toolForm.headers_str`，并把解析结果存进本地 `parsed` ref；显示识别结果卡（方法/地址/路径参数/query/body/headers）
- 警告逐条列出：不支持的方法、非 JSON 请求体、自动补 https 等
- **POST/PUT/PATCH/DELETE 时**显示橙色警告：平台把本表工具按只读使用，但**请求会真实发出**，请确认是查询接口
- 解析成功后进入下一步时，用 `buildDraftParams` 生成参数清单**并写一次** `parameter_schema_str`；此后**不再自动重算**

**② 起名与描述**
- 工具名称（`toolForm.name`）、描述（`toolForm.description`）、业务分组（`toolForm.group_name`）
- 描述下方给写法提示：写清「什么场景下用它」比「它做什么」更重要

**③ 确认参数**
- 若第 ① 步在解析之后又被改过（用一个 `staleParams` ref 标记：解析成功时置 `false`，第 ① 步的粘贴框内容变化时置 `true`），显示橙色提示条：
  「你在第 1 步改过请求地址：这里的参数可能已过期。**不会自动覆盖**你手改的内容。」+「重新生成参数」按钮
- 「重新生成参数」是**唯一**会覆盖参数的操作，只有用户点击才执行
- 参数清单用 `<ToolParamEditor v-model="toolForm.parameter_schema_str" mode="wizard" :locations="locationMap" />` 渲染
- 上方一行汇总：「从你的请求里识别出 N 个参数：X 个必填、Y 个可选」
- `locationMap` 由 `buildLocationMap(draftParams)` 得来

**④ 试跑一次**
- 内嵌 `<ApiToolTester :method="toolForm.method" :url-template="toolForm.url_template" :headers-str="toolForm.headers_str" :schema-str="toolForm.parameter_schema_str" />`

- [ ] **Step 2: 类型检查**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | tail -5`
Expected: 零新增错误。

---

## Task 9: `ToolRegistry.vue` 接入向导 tab

**Files:**
- Modify: `frontend/src/components/system/ToolRegistry.vue`

- [ ] **Step 1: 加 tab 状态与切换**

在 `<script setup>` 加：

```ts
// 弹窗内的两种填写方式：表单模式是全字段平铺（原有交互），向导模式是 4 步引导。
// 两者**共用同一份 toolForm**——向导只是填它的另一种交互，切 tab 不丢内容。
const editorMode = ref<'form' | 'wizard'>('form')
```

`openModal()` 里在 `showModal.value = true` 之前加一行：`editorMode.value = 'form'`（默认进表单模式，保持既有路径不乱；向导由用户主动点）。

- [ ] **Step 2: 模板加分段切换**

把弹窗标题那一行改为标题 + 分段切换：

```html
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <h3 class="text-lg font-bold text-gray-900">{{ isEditing ? '编辑工具' : '添加新工具' }}</h3>
                    <div class="flex bg-gray-100 p-0.5 rounded-lg text-xs" role="group" aria-label="填写方式">
                        <button
                            type="button"
                            class="px-2.5 py-1.5 rounded-md transition-all font-medium"
                            :class="editorMode === 'form' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                            @click="editorMode = 'form'"
                        >表单模式</button>
                        <button
                            type="button"
                            class="px-2.5 py-1.5 rounded-md transition-all font-medium inline-flex items-center gap-1"
                            :class="editorMode === 'wizard' ? 'bg-white shadow-sm text-primary' : 'text-gray-400'"
                            @click="editorMode = 'wizard'"
                        >向导模式<span class="rounded-full bg-primary/10 px-1.5 text-[11px] font-semibold text-primary">推荐小白使用</span></button>
                    </div>
                </div>
```

- [ ] **Step 3: 表单模式复用 ToolParamEditor，向导模式挂 ToolWizard**

1. import：

```ts
import ToolParamEditor from './ToolParamEditor.vue'
import ToolWizard from './ToolWizard.vue'
```

2. 把原参数编辑区那一整块（第 665-721 行）替换为：

```html
                    <ToolParamEditor v-model="toolForm.parameter_schema_str" />
```

3. 在 `<div class="space-y-4">` 外层按模式分流——**表单模式保留原有全部字段**，向导模式只渲染向导：

```html
                <ToolWizard v-if="editorMode === 'wizard'" v-model="toolForm" />
                <div v-else class="space-y-4">
                    <!-- 原有 8 个字段，参数区已换成 <ToolParamEditor> -->
                </div>
```

4. `saveTool()` **完全不动**——它仍是唯一的保存入口，两个 tab 走同一条路径。

- [ ] **Step 4: 清掉搬走后再无引用的本地状态**

从 `ToolRegistry.vue` 删除已移入 `ToolParamEditor.vue` 的：`schemaEditorMode`、`visualParams`、`parsedSchema`、`visualParamSeed`、`nextVisualParamKey`、`loadVisualFromJson`、`buildSchemaJson`、`addVisualParam`、`removeVisualParam`、`switchSchemaMode`、`visualNestedCount`，以及不再使用的 `PARAM_TYPE_OPTIONS` / `parseToolSchema` / `serializeToolSchema` / `ParsedToolSchema` / `SchemaParam` import。

**注意**：`openModal()` 里的 `loadVisualFromJson()` 调用要一并删掉（组件自己会在挂载时解析）。

- [ ] **Step 5: 类型检查 + 契约测试**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | tail -5`
Run: `.venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3`
Expected: 类型零新增；前端全绿（此时向导契约测试尚未写）。

---

## Task 10: 向导契约测试

**Files:**
- Create: `tests/frontend/test_api_tool_wizard_contract.py`

- [ ] **Step 1: 写契约测试**

创建 `tests/frontend/test_api_tool_wizard_contract.py`，断言（每条都要能在实现里指出对应位置）：

```python
"""API 工具「向导模式」契约（静态源码断言）。

钉住的都是**改坏了会静默出错**的地方：
1. 向导不得有自己的数据模型（出现 ``wizardForm`` 就是分叉的开始）；
2. 「重新解析」不得自动覆盖用户手改过的参数（静默丢数据）；
3. 试跑前必须拦住未填的必填参数（不让带 ``{city}`` 的请求发出去）；
4. 试跑失败必须分清「接口拒绝了」与「根本没连上」（这两种给的建议完全不同）；
5. 私网地址必须提示但不阻止（与运行时同权的既定决策）。
"""
```

必须包含的断言（逐条）：

1. `test_registry_exposes_both_editor_modes`：`ToolRegistry.vue` 里有 `editorMode`、`>表单模式</button>`、`>向导模式`。
2. `test_wizard_has_no_second_data_model`：`ToolWizard.vue` 源码里 **不含** `wizardForm`，且含 `v-model` 与 `toolForm`。
3. `test_wizard_has_four_steps`：`ToolWizard.vue` 里有「粘贴请求」「起名与描述」「确认参数」「试跑一次」四个步骤文案，且用 `buildDraftParams`。
4. `test_reparse_never_overwrites_params_automatically`：`ToolWizard.vue` 里「重新生成参数」是**按钮点击**触发；断言 `regenerateParams` 函数存在且被 `@click` 绑定，同时断言它在 `parseRequestInput` 的成功分支里**没有**被调用（用 `_body(source, "const applyParsed")` 取块，断言块内不含 `regenerateParams(`）。
5. `test_wizard_uses_shared_param_editor`：`ToolWizard.vue` 里有 `<ToolParamEditor`，并传了 `mode="wizard"`。
6. `test_tester_blocks_missing_required_params`：`ApiToolTester.vue` 里有 `missingRequired` 且 `runPreview` 里在发请求前有 `if (missingRequired.value.length > 0)` 的提前 return。
7. `test_tester_distinguishes_rejection_from_unreachable`：`ApiToolTester.vue` 同时含「地址是通的，但接口拒绝了这次请求」与「根本没连上目标接口」。
8. `test_tester_warns_about_private_targets_without_blocking`：`ApiToolTester.vue` 含 `isPrivateTarget`，且模板里有该提示（断言 `v-if="isPrivateTarget"`），并且 `runPreview` **不因**私网而提前 return（断言 `isPrivateTarget` 不出现在 `runPreview` 函数体内）。
9. `test_tester_says_params_are_not_persisted`：含「测试值不会保存到工具配置里」。
10. `test_post_is_flagged_as_really_sent`：`ToolWizard.vue` 里对非 GET 的警告含「请求会真实发出」。
11. `test_wizard_passes_draft_to_preview_api`：`ApiToolTester.vue` 调 `toolApi.preview`，且请求体含 `url_template` 与 `params`。
12. `test_stale_params_hint_exists`：`ToolWizard.vue` 含「不会自动覆盖」。

- [ ] **Step 2: 跑测试**

Run: `.venv/bin/python -m pytest tests/frontend/test_api_tool_wizard_contract.py -q`
Expected: 12 passed（若某条失败，先确认是实现缺了，而不是断言写错——**不许为了让测试变绿而删断言**）。

- [ ] **Step 3: 全量前端 + 类型检查**

Run: `.venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3`
Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | tail -5`
Expected: 前端测试 = 基线 + 本次新增条数；类型错误零新增。

---

## Task 11: 反向验证（变异测试）

**Files:** 无（改完即还原）

对每条新守卫做一次「破坏 → 用例必须失败 → 还原 → 用例必须通过」，**每个变异前后做字节级备份与还原校验**（`md5` 或 `diff` 确认无残留）：

- [ ] `toolMethods.ts` 里把 `PATCH` 删掉 → `test_static_list_excludes_unsupported_methods` 必须失败
- [ ] `ToolRegistry.vue` 下拉改回写死字面量 → `test_registry_form_dropdown_uses_shared_list` 必须失败
- [ ] `ToolRegistry.vue` 筛选器去掉 `tools.value.map` → `test_registry_filter_keeps_data_driven_extras` 必须失败
- [ ] `toolDraft.ts` 的 `resolveParamLocation` 把 path 判定挪到 GET 之后 → Node 往返用例必须失败
- [ ] `toolDraft.ts` 把「URL 带值 → 可选」改成「一律必填」→ Node 往返用例必须失败
- [ ] `generic_api.py` 把 `_execute_request` 里 204 分支挪到非 2xx 检查之后 → 后端契约必须失败
- [ ] 试跑端点把 `raise_on_http_error=False` 删掉 → `test_preview_classifies_http_error` 必须失败
- [ ] 试跑端点去掉占位符预检 → `test_preview_refuses_unfilled_placeholders_without_sending` 必须失败
- [ ] `ToolWizard.vue` 在解析成功分支里加一句 `regenerateParams()` → `test_reparse_never_overwrites_params_automatically` 必须失败
- [ ] `ApiToolTester.vue` 把私网判断塞进 `runPreview` 的提前 return → `test_tester_warns_about_private_targets_without_blocking` 必须失败

---

## Task 12: 收尾

**Files:**
- Modify: `tests/CHECKLIST.md`

- [ ] **Step 1: 全量前端回归**

Run: `.venv/bin/python -m pytest tests/frontend -q 2>&1 | tail -3`
Expected: 全绿。

- [ ] **Step 2: 后端回归**

Run: `.venv/bin/python -m pytest tests/api -q 2>&1 | tail -3`
Expected: 与基线一致 + 本次新增。

- [ ] **Step 3: 类型检查归一化 diff**

Run: `cd frontend && node_modules/.bin/vue-tsc --noEmit 2>&1 | sort > /tmp/ts-after.txt && wc -l /tmp/ts-after.txt`
Expected: 与 Task 0 抓下来的基线做归一化 diff 为空（零新增、零消失），而不是只看总数。

- [ ] **Step 4: 更新 CHECKLIST**

在 `tests/CHECKLIST.md` **开头的表格**（最新在最上）加一行，列全：`frontend/src/utils/toolMethods.ts`、`toolImport.ts`、`toolDraft.ts`、`frontend/src/components/system/ToolParamEditor.vue`、`ApiToolTester.vue`、`ToolWizard.vue`、`ToolRegistry.vue`、`frontend/src/api/tool.ts`、`app/services/ai/tools/generic_api.py`、`app/schemas/tool.py`、`app/api/portal/endpoints/tools.py`、新增的 4 个测试文件、spec 与 plan 文档。

内容必须包含（本仓库 CHECKLIST 的惯例）：

- 用户需求原文（截图反馈：想在添加工具页加一个向导模式 tab，方便小白）；
- 本次的设计边界与**反直觉发现**（运行时参数缺省必填、`required: []` 表达不了全可选、方法清单三处分裂、审计中间件自动留痕且 `Authorization` 会被脱敏）；
- 关键取舍及其理由（不做显示层脱敏、试跑与运行时同权不加地址限制、重新解析不自动覆盖）；
- 精确测试数字：前端「基线 → 现状」、后端「基线 → 现状」、vue-tsc 归一化 diff 为空；
- **反向验证逐条结果**（哪条变异被哪条用例捕获，有无漏网及原因）；
- 明确写明：未执行 `git commit`、未代跑 `./dev.sh`；前端需重新构建、后端需重启后生效。

- [ ] **Step 5: 汇报（不提交）**

向用户汇报：改了哪些文件、每条测试的实际数字、反向验证结果、以及「需要重新构建前端 + 重启后端才生效」。**不要执行 git commit。**

---

## 自检（写完计划后逐项核对）

- **Spec 覆盖**：D1（4 步向导）→ Task 8/9；D2（URL + curl）→ Task 2；D3（同权试跑 + 私网提示 + 审计）→ Task 5/6；D4（新建 + 编辑都可用）→ Task 9（`openModal` 不分新建/编辑）；D5（共享 `ToolParamEditor`）→ Task 7；D6（不脱敏）→ 无需改动（保持现状即符合）；D7（方法清单收敛）→ Task 1。§6 的「不自动覆盖」→ Task 8 Step ③ + Task 10 断言 4 + Task 11 变异。§8.2 的「同口径」→ Task 4/5 + Task 11 变异。§10 的测试策略 → Task 1/2/3/5/7/10。
- **占位符扫描**：无 TBD/TODO；每个代码步骤都给了可直接落盘的代码。
- **类型一致性**：`parseRequestInput -> ParsedRequest | ParseFailure`（Task 2）→ `buildDraftParams(parsed: ParsedRequest)`（Task 3）→ `ApiToolTester` props（Task 6）→ 后端 `SysApiToolPreviewRequest/Response` 字段名（Task 5）与前端 `SysApiToolPreviewPayload/Result`（Task 6）逐字段对齐：`method` / `url_template` / `headers` / `parameter_schema` / `params` 与 `ok` / `status_code` / `elapsed_ms` / `body` / `body_truncated` / `error_kind` / `message`。

---

## 实施偏差记录（实现完成后补记）

计划写完之后、动手之前看不出来的地方。**保留原计划文本不改**，偏差记在这里——计划是当时的判断，偏差是事实。

1. **`pathParams` 的语义写错了（Task 2）**。原计划让 `parseUrl` 把 query 里的 `{b}` 也塞进 `pathParams`。实现时想清楚了：运行时的 `url_template` **已被剥掉 query**，路径替换只作用于路径部分，`?b={b}` 里的 `b` 就是一个普通查询参数。塞进 `pathParams` 会让 `buildDraftParams` 把它标成「路径参数」，界面落位显示错误。已改为**只装路径里的占位符**，并新增一条 Node 用例专门守护。
2. **`buildDraftParams` 会把参数整个丢掉（Task 3）**。原计划在 query/body 循环里写了 `if (isPlaceholderValue(item.value)) continue`——那会让 `?b={b}` 这个参数从清单里消失。已删除该分支（占位符只是「需要填值」，被 `push` 内的必填规则正确处理），并新增用例守护。
3. **body 数字类型断言写错了（Task 3）**。原计划断言 `["amount", "100", "number"]`，而实现按整数/小数区分返回 `integer`／`number`（刻意如此，类型精度对模型有用）。测试按实现修正。
4. **跨文件导入必须带 `.ts` 扩展名（Task 2/3）**。`node --experimental-strip-types` 的 ESM 解析不做扩展名补全，`from './toolMethods'` 会 `ERR_MODULE_NOT_FOUND`。仓库里被 Node 直跑的纯函数模块本来就这么写（`src/utils/chatSessionExport.ts`、`generatedFilePreviewPlan.ts`），照办即可——原计划漏了这一点，并已把它变成一条断言。
5. **`UnsupportedMethodError` 的文案（Task 4）**。原计划的用例预期 `[Execution Error] Unsupported method: HEAD`，但历史实现是**直接 `return`** 而非抛异常，因此从不经过 `except` 分支，真实文案是 `[Error] Unsupported method: HEAD`。抽取后新增单独的 `except UnsupportedMethodError` 分支保住原文案，断言同步修正。
6. **非 GET + query 参数的落位警告（Task 2，计划未覆盖）**。`url_template` 剥掉 query 后，非 GET 的其余参数一律进 JSON body，所以 `curl -X POST '...?flag=1'` 的「查询参数」实际会变成请求体字段。这不是 bug 而是既有语义，但必须让使用者知道——已在解析结果里补一条 warning 并加用例。
7. **发现并修掉一条自己写出的假绿用例（Task 5）**。`test_preview_does_not_touch_tool_table` 最初只断言「工具表行数不变」——端点还不存在时返回 405，行数当然不变，于是它**通过了**。已补 `assert response.status_code == 200` 与 `ok is True`，让「什么都没发生」必须建立在「请求真的打到了端点」之上。
8. **两个 `except:` 收紧为 `except Exception:`（Task 4）**。重构时顺手改的：裸 `except:` 会连 `KeyboardInterrupt` / `SystemExit` 一起吞掉。对 `json.loads` 与 `response.json()` 而言行为等价，但不属于「逐字节不变」，如实记录。
9. **`test_imported_tool_schema_is_loaded_by_shape_not_assumption` 换了断言对象（Task 7）**。原计划要它继续断言 `openModal` 里调用 `loadVisualFromJson`——但编辑器抽成组件后，解析职责搬进了组件（组件自己在 `modelValue` 变化时重新同步），父组件的 `openModal` 不再需要预解析。断言改为指向 `ToolParamEditor.vue` 的 `loadVisualFromJson`，并额外要求存在对 `modelValue` 的 `watch` 同步。
10. **顺手修掉一个审计脱敏缺口（计划外）**。验证「试跑会留审计」时实测发现：审计中间件会把**每个**请求的请求体落库并脱敏，而脱敏名单是按**下划线**书写的（`api_key`），HTTP 头却习惯用**连字符**（`X-Api-Key`）——两者不匹配，于是试跑面板引导用户填的 `X-Api-Key` 会**明文写进审计库**（`Authorization` 只是恰好被 FULL 名单精确命中，才显得没事）。修复：键名比对前归一化（`-` 与空格 → `_`）、把 `auth_token` / `authtoken` 补进子串名单、正则同步接受连字符。`Content-Type` / `Accept` / `X-Request-Id` / `cache_key` / `sort_key` 均未被误伤（有参数化用例守着）。**已知边界（未改）**：非 JSON 的 `key: value`（冒号后带空格）文本形态仍不脱敏——那是既有行为，且不在审计主路径上（审计落的是 JSON 请求体或 `&` 分隔的 query）；若放开值匹配到允许空格，会贪婪吞掉后续内容、反过来毁掉审计可用性。
11. **内核契约测试下沉到 mock httpx（Task 11）**。原计划用 `AsyncMock` 替掉 `_perform_request`，可那只能证明「格式化分支没改」，证明不了路径替换 / 参数落位 / headers / 非 2xx 抛错这条整链路——而这恰恰是「试跑与运行时同源」的实质。已改为替换 `httpx.AsyncClient`，让用例必须走完真实内核；用例数 8 → 12（新增路径参数剔除、非 GET body、headers 透传、`trust_env`/超时）。
12. **补了一份「新旧实现逐字节对比」的一次性证据（Task 11）**。从 `git HEAD` 取出旧版 `generic_api.py` 动态加载，与新实现跑同一组 10 个场景（中文 JSON、纯文本、POST body、PATCH、204、404 截断、路径替换、HEAD、连接失败、超时），比对**返回值**与**实际发出的 URL / 参数 / headers / client 配置**——全部一致，含 `[Error] Unsupported method: HEAD` 这个易失文案。
13. **用户实测打回一个真 bug：预检误杀了已填的路径参数（实现完成后返工）**。第 ④ 步试跑时，用户把 `order_id` 填成 `11333`，却收到「地址里还有没填的参数：`{order_id}`」。根因是预检写成了「模板里还有没有 `{...}`」，**根本没看 `params`**——防呆防成了防所有人。已改为「模板里有占位符**且** `params` 里没有该键、或值为空白」才算没填，判定口径与运行时的路径替换（遍历 `params` 找 `{key}`）对齐。补 3 条用例：已填必须放行、多个占位符只报没填的那个、空白串算没填而数字 `0` 是有效值（不能被 falsy 判断吃掉）。**教训**：原用例只覆盖了「没填要拦住」，没覆盖「填了就不能拦」——单向断言的测试，方向反了照样全绿；这正是「反向验证」要防的事，而它没被前 21 条变异覆盖，说明变异清单本身也会漏。

## 反向验证结果（Task 11 实跑）

22 条变异**全部被捕获**，每条变异前后以 md5 校验字节还原、无一残留（编号与最终脚本一致）：

| # | 变异 | 捕获它的用例 |
|---|---|---|
| ① | 静态方法清单删掉 PATCH | `test_api_tool_method_list_contract` |
| ② | 表单下拉改回写死字面量 | `test_registry_form_dropdown_uses_shared_list` |
| ③ | 筛选器丢掉「既有数据里出现过的方法」 | `test_registry_filter_keeps_data_driven_extras` |
| ④ | 落位判定把 path 让位给方法判定 | `tool_draft_roundtrip.mjs` |
| ⑤ | 参数一律判为必填 | `tool_draft_roundtrip.mjs` |
| ⑥ | 运行时不支持的方法文案退化 | `test_generic_api_runtime_contract` |
| ⑦ | 路径参数不再从 query 里剔除 | `test_path_params_are_substituted_and_excluded_from_query` |
| ⑧ | 传输层改为信任环境代理 | `test_transport_disables_env_proxy_and_keeps_30s_timeout` |
| ⑨ | 试跑改为「非 2xx 即抛」（丢掉原始响应） | `test_preview_passes_in_memory_draft_shaped_like_runtime` |
| ⑩ | 试跑去掉未填占位符预检 | `test_preview_refuses_unfilled_placeholders_without_sending` |
| ⑪ | 试跑端点换成更宽的权限依赖 | `test_preview_endpoint_requires_same_permission_as_writes` |
| ⑫ | 审计中间件不再覆盖 `/api/` 全部路径 | `test_preview_request_lands_in_audit_trail` |
| ⑬ | 脱敏键名不做连字符归一化 | `test_http_header_style_credential_keys_are_masked` |
| ⑭ | 解析成功后自动重算参数（静默覆盖手改） | `test_reparse_never_overwrites_params_automatically` |
| ⑮ | 私网地址变成试跑拦截条件 | `test_tester_warns_about_private_targets_without_blocking` |
| ⑯ | 参数编辑器允许给必填项填默认值 | `test_default_value_only_applies_to_optional_params` |
| ⑰ | 「下一步」绕过步骤门禁 | `test_wizard_blocks_forward_until_the_step_is_complete` |
| ⑱ | 步骤条改回折行布局 | `test_wizard_steps_stay_on_a_single_row` |
| ⑲ | 过期标记改回「watch 触发过就算」 | `test_inserting_the_example_does_not_falsely_mark_params_stale` |
| ⑳ | 参数描述门禁改用解析快照 | `test_step_three_checks_the_live_schema_not_the_parse_snapshot` |
| ㉑ | tab 选中态退回低对比样式 | `test_tab_switch_has_a_visible_selected_state` |
| ㉒ | 试跑预检只看模板、不看 `params`（用户实测打回的那个 bug） | `test_preview_accepts_path_placeholders_that_already_have_values` |

另有「运行时输出逐字节不变」的直接证据（一次性脚本，不属变异）：从 `git HEAD` 取旧版 `generic_api.py` 动态加载，与新实现跑同一组 10 个场景（中文 JSON、纯文本、POST body、PATCH、204、404 截断、路径替换、HEAD、连接失败、超时），比对返回值与**实际发出的 URL / 参数 / headers / client 配置**——全部一致。
