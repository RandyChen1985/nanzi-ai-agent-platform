"""MCP「其他 Header」绑凭据后的序列化行为。

为什么需要这一组：绑定是 ``(consumer_id, slot)`` 维度的，值根本不进服务配置，
但**Header 名必须进**。运行时 ``apply_mcp_bindings`` 的循环是

    for name in list(resolved):        # resolved = 服务配置里的 auth_headers
        ... result = resolve(slot=f"header:{name}")

只遍历配置里已存在的 Header 名。名字不在配置里，``resolve`` 一次都不会被调用，
绑定永远查不到——而界面上显示的是「来自凭据 xxx」，看起来完全正常。

旧实现要求「值非空」才写进配置，于是「只绑凭据、不填值」的行被整条丢掉。
这一组测试钉住修复后的规则：**值空但有绑定 → 仍然写进配置，值留空串**。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/mcpAuthHeaders.ts"
pytestmark = pytest.mark.no_infrastructure

HARNESS = """
(async () => {
const fs = require('fs');
const path = require('path');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync(path.resolve(__MODULE__), 'utf8');
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const moduleRef = { exports: {} };
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => { __EXPRESSION__ })();
process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error); process.exit(1); });
"""


def _run(expression: str):
    script = HARNESS.replace("__MODULE__", json.dumps(MODULE)).replace("__EXPRESSION__", expression)
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_bound_header_with_empty_value_is_kept_at_creation():
    """新建态：值空但绑了凭据的 Header 必须写进 auth_headers，值留空串。

    空串是刻意的：后端 ``_parse_auth_headers`` 不过滤空 value，运行时
    ``_looks_unfilled('')`` 为真，凭据中心正好会去补这个槽位。
    """
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'X-Tenant', value: '' }, { key: 'X-Trace', value: '' }],
  (name) => name === 'X-Tenant',
);
return { headers: plan.headers, patch: plan.patch };
"""
    )

    assert result["headers"] == {"X-Tenant": ""}, "绑了凭据的空值 Header 必须在配置里"
    assert "X-Trace" not in result["headers"], "没绑凭据的空值行照旧丢弃"


def test_unbound_empty_row_is_still_dropped():
    """没绑凭据的空行不进配置——否则会凭空多出空 Header 发给对端。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'X-Nothing', value: '   ' }],
  () => false,
);
return { headers: plan.headers, patch: plan.patch };
"""
    )

    assert result["headers"] == {}
    assert result["patch"] == {}


def test_authorization_never_enters_other_headers():
    """Authorization 走独立开关，混进其他 Header 会被后端 400 拒绝。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'authorization', value: 'Bearer x' }, { key: 'X-Ok', value: 'v' }],
  () => true,
);
return { headers: plan.headers };
"""
    )

    assert result["headers"] == {"X-Ok": "v"}


def test_new_row_with_bound_credential_reaches_patch():
    """编辑态新增一行、只绑凭据不填值，也要进 patch（否则服务配置里没有这个 Header）。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'X-Api-Key', value: '' }],
  () => true,
);
return { patch: plan.patch, headers: plan.headers };
"""
    )

    assert result["patch"] == {"X-Api-Key": ""}
    assert result["headers"] == {"X-Api-Key": ""}


def test_existing_row_untouched_stays_out_of_patch():
    """已存在的行没改动 → 不发 patch，后端保留原配置（这条本来就能注入）。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'X-Tenant', value: '', existing: true, changed: false }],
  () => true,
);
return { patch: plan.patch };
"""
    )

    assert result["patch"] == {}


def test_existing_row_edited_to_empty_with_binding_is_patched():
    """已存在的行被编辑且绑了凭据 → 发空串，确保该 Header 名在配置里存在。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'X-Tenant', value: '', existing: true, changed: true }],
  () => true,
);
return { patch: plan.patch };
"""
    )

    assert result["patch"] == {"X-Tenant": ""}


def test_removed_row_sends_null_even_when_bound():
    """删掉一行时发 null 解除，不能因为还留着绑定就把空串写回去。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [{ key: 'X-Tenant', value: '', existing: true, removed: true, changed: true }],
  () => true,
);
return { patch: plan.patch };
"""
    )

    assert result["patch"] == {"X-Tenant": None}


def test_plain_non_credential_headers_keep_working():
    """值和凭据都正常的老路径不能被改坏。"""
    result = _run(
        """
const plan = api.planMcpAuthHeaders(
  [
    { key: ' X-Tenant ', value: ' t-1 ' },
    { key: 'X-Api-Key', value: 'k-1', existing: true, changed: true },
    { key: '', value: 'ignored' },
  ],
  () => false,
);
return { headers: plan.headers, patch: plan.patch };
"""
    )

    assert result["headers"] == {"X-Tenant": "t-1", "X-Api-Key": "k-1"}
    # 未落库的行同时进 patch：编辑态新增一行时靠它落地
    assert result["patch"] == {"X-Tenant": "t-1", "X-Api-Key": "k-1"}, "名称两端空格要归一，空 key 丢弃"
