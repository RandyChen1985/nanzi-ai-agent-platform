"""换账号时侧栏菜单必须跟着身份走（`frontend/src/composables/useNavigation.ts`）。

这份测试跑的是**真实模块**（node + typescript 现场转译，mock 掉 axios 与 localStorage），
不是对源码做字符串断言。原因是这个 bug 的形态是竞态：任何"看起来对"的代码都可能
在旧请求迟到时把上一个用户的树写回快照。

钉住四个行为：

1. 快照属于上一个身份（`user_id` 不同）时必须丢弃，并重新拉全量；
2. 登出前发出的静默校验即使晚到，也不能覆盖新用户的树与快照；
3. 条件请求必须同时带 `version` 与 `signature`，`unchanged` 才允许复用；
4. 没有 `user_id` 的旧格式快照一律丢弃（升级后第一次进后台必然全量刷新）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/composables/useNavigation.ts"
pytestmark = pytest.mark.no_infrastructure

HARNESS = """
(async () => {
const fs = require('fs');
const path = require('path');
const ts = require('./frontend/node_modules/typescript');

const storage = new Map();
globalThis.localStorage = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
  clear: () => storage.clear(),
  key: (i) => Array.from(storage.keys())[i] ?? null,
  get length() { return storage.size; },
};

const axiosMock = {
  calls: [],
  responder: null,
  async get(url, config) {
    const params = (config && config.params) || {};
    axiosMock.calls.push({ url, params });
    if (!axiosMock.responder) throw new Error('no responder');
    return axiosMock.responder(params, axiosMock.calls.length);
  },
};
// TS 的 `import axios from '../utils/axios'` 编译为 `axios_1.default`，mock 必须同时
// 提供 default 与 __esModule，否则拿到的是 undefined（表现为「请求根本没发出去」）。
axiosMock.default = axiosMock;
axiosMock.__esModule = true;

const loaded = new Map();
const loadModule = (absolute) => {
  if (loaded.has(absolute)) return loaded.get(absolute).exports;
  const source = fs.readFileSync(absolute, 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const moduleRef = { exports: {} };
  loaded.set(absolute, moduleRef);
  const localRequire = (id) => {
    if (/utils\\/axios$/.test(id)) return axiosMock;
    if (id === 'vue') return require(path.resolve('frontend/node_modules/vue'));
    if (id.startsWith('.')) {
      const base = path.resolve(path.dirname(absolute), id);
      const hit = [base, base + '.ts', base + '.js'].find(
        (candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile(),
      );
      if (hit) return loadModule(hit);
    }
    return require(id);
  };
  new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, localRequire);
  return moduleRef.exports;
};

const api = loadModule(path.resolve(__MODULE__));
const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tree = (code) => [{ code, node_type: 'group', children: [] }];
const snapshotOf = () => {
  const raw = storage.get('nav_snapshot_v1');
  return raw ? JSON.parse(raw) : null;
};

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


VERSION = 2_000_002


def test_snapshot_owned_by_another_user_is_discarded():
    """别人的快照不能拿来首屏渲染，也不能作为条件请求的 version 来源。"""
    result = _run(
        f"""
storage.set('user_info', JSON.stringify({{ id: 2, role: 'user' }}));
storage.set('nav_snapshot_v1', JSON.stringify({{
  version: {VERSION}, signature: 'sig-user-1', user_id: 1,
  nodes: tree('g-admin'), landing_path: '/dashboard', fetched_at: 1,
}}));
axiosMock.responder = () => ({{ data: {{
  version: {VERSION}, signature: 'sig-user-2', unchanged: false,
  nodes: tree('g-work'), landing_path: '/dashboard/workbench',
}} }});
await api.ensureNavigation();
const view = api.useNavigation();
return {{
  calls: axiosMock.calls.map(c => c.params),
  nodeCodes: view.groups.value.map(n => n.code),
  snapshot: snapshotOf(),
}};
"""
    )

    assert result["calls"][0] == {}, "别人的快照不能带 version 做条件请求"
    assert result["nodeCodes"] == ["g-work"]
    assert result["snapshot"]["user_id"] == "2"
    assert result["snapshot"]["signature"] == "sig-user-2"


def test_snapshot_without_owner_is_discarded():
    """升级前写入的旧快照没有 user_id：一律丢弃，避免"谁留着算谁的"。"""
    result = _run(
        f"""
storage.set('user_info', JSON.stringify({{ id: 7, role: 'user' }}));
storage.set('nav_snapshot_v1', JSON.stringify({{
  version: {VERSION}, nodes: tree('g-legacy'), landing_path: '/dashboard', fetched_at: 1,
}}));
axiosMock.responder = () => ({{ data: {{
  version: {VERSION}, signature: 'sig-7', unchanged: false,
  nodes: tree('g-fresh'), landing_path: '/dashboard/workbench',
}} }});
await api.ensureNavigation();
const view = api.useNavigation();
return {{ calls: axiosMock.calls.map(c => c.params), nodeCodes: view.groups.value.map(n => n.code) }};
"""
    )

    assert result["calls"][0] == {}
    assert result["nodeCodes"] == ["g-fresh"]


def test_late_response_from_previous_user_cannot_overwrite_new_user():
    """登出前发出的静默校验晚到，不能把上一个用户的菜单写回 state 与快照。"""
    result = _run(
        f"""
storage.set('user_info', JSON.stringify({{ id: 1, role: 'admin' }}));
storage.set('nav_snapshot_v1', JSON.stringify({{
  version: {VERSION}, signature: 'sig-admin', user_id: 1,
  nodes: tree('g-admin'), landing_path: '/dashboard', fetched_at: 1,
}}));
axiosMock.responder = async (params, n) => {{
  if (n === 1) {{
    await tick(120);
    return {{ data: {{
      version: {VERSION}, signature: 'sig-admin', unchanged: false,
      nodes: tree('g-admin'), landing_path: '/dashboard',
    }} }};
  }}
  await tick(10);
  return {{ data: {{
    version: {VERSION} + 1, signature: 'sig-user-2', unchanged: false,
    nodes: tree('g-work'), landing_path: '/dashboard/workbench',
  }} }};
}};

// 管理员在后台：hydrate 快照并发出静默校验（旧请求还在飞）
await api.ensureNavigation();
// 退出登录 → 用新账号登录
storage.set('user_info', JSON.stringify({{ id: 2, role: 'user' }}));
api.clearNavigationSnapshot();
await api.ensureNavigation();
await tick(250);

const view = api.useNavigation();
return {{
  calls: axiosMock.calls.map(c => c.params),
  nodeCodes: view.groups.value.map(n => n.code),
  landing: view.landingPath.value,
  snapshot: snapshotOf(),
}};
"""
    )

    assert result["nodeCodes"] == ["g-work"], "迟到的管理员响应覆盖了新用户的菜单"
    assert result["landing"] == "/dashboard/workbench"
    assert result["snapshot"]["user_id"] == "2"
    assert result["snapshot"]["signature"] == "sig-user-2"


def test_conditional_request_carries_version_and_signature():
    """`unchanged` 必须建立在 version + signature 双命中上。"""
    result = _run(
        f"""
storage.set('user_info', JSON.stringify({{ id: 3, role: 'user' }}));
storage.set('nav_snapshot_v1', JSON.stringify({{
  version: {VERSION}, signature: 'sig-3', user_id: 3,
  nodes: tree('g-cache'), landing_path: '/workbench', fetched_at: 1,
}}));
axiosMock.responder = () => ({{ data: {{
  version: {VERSION}, signature: 'sig-3', unchanged: true,
}} }});
await api.ensureNavigation();
await tick(50);
const view = api.useNavigation();
return {{
  calls: axiosMock.calls.map(c => c.params),
  nodeCodes: view.groups.value.map(n => n.code),
}};
"""
    )

    assert result["calls"][0] == {"version": VERSION, "signature": "sig-3"}
    assert result["nodeCodes"] == ["g-cache"], "unchanged 时应保留快照（不重渲染）"


def test_response_for_a_previous_identity_is_dropped_even_without_logout():
    """跨标签换号：本标签没走登出，但响应发出后身份已被换掉，也不能落盘。"""
    result = _run(
        f"""
storage.set('user_info', JSON.stringify({{ id: 1, role: 'admin' }}));
axiosMock.responder = async () => {{
  await tick(80);
  return {{ data: {{
    version: {VERSION}, signature: 'sig-admin', unchanged: false,
    nodes: tree('g-admin'), landing_path: '/dashboard',
  }} }};
}};
const pending = api.ensureNavigation();
await tick(10);
// 另一个标签页登录了新账号：本标签没有调用 clear，只共享 localStorage
storage.set('user_info', JSON.stringify({{ id: 2, role: 'user' }}));
await pending;
const view = api.useNavigation();
return {{ nodeCodes: view.groups.value.map(n => n.code), snapshot: snapshotOf() }};
"""
    )

    assert result["nodeCodes"] == [], "上一个人身份的响应被渲染到了新身份上"
    assert result["snapshot"] is None, "上一个人身份的响应被写进了快照"
