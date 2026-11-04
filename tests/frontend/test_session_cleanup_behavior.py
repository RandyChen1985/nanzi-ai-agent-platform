"""登出清理必须覆盖所有「属于某个身份」的本地副本。

2026-11 排查发现的两类残留：

- `localStorage.nanzi_mcp_recent_tokens` 明文保存 MCP 会话令牌，登出后仍在；
- `sessionStorage` 里的嵌入会话令牌（`nzi_embed_personal_session_token:*`）
  以前完全不在清理范围内。

同时钉住反面：主题、侧栏折叠这类**设备级偏好**必须保留，否则换个账号整个界面
会跳回默认值，用户会当成另一个 bug。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/sessionCleanup.ts"
pytestmark = pytest.mark.no_infrastructure

HARNESS = """
(async () => {
const fs = require('fs');
const path = require('path');
const ts = require('./frontend/node_modules/typescript');

const makeStorage = () => {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    key: (i) => Array.from(map.keys())[i] ?? null,
    get length() { return map.size; },
    dump: () => Object.fromEntries(map),
  };
};
const local = makeStorage();
const session = makeStorage();
globalThis.window = { localStorage: local, sessionStorage: session };

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


def test_identity_scoped_storage_is_cleared_and_device_preferences_are_kept():
    result = _run(
        """
local.setItem('user_info', '{"id":1}');
local.setItem('nav_snapshot_v1', '{}');
local.setItem('nanzi_mcp_recent_tokens', '[]');
local.setItem('workbench_pinned_agent_ids', '[]');
local.setItem('knowledge_portal_pinned', '1');
local.setItem('knowledge_ragflow_metadata_top_k', '5');
local.setItem('dataset_portal_keep_open', '1');
local.setItem('nanzi.skillCreatedBanner.notified:skill-1', '1');
session.setItem('nzi_embed_personal_session_token', 'emb-1');
session.setItem('nzi_embed_personal_session_token:inst-2', 'emb-2');
session.setItem('dismiss_pwd_banner_7', '1');

// 设备级偏好：必须留下
local.setItem('nanzi_app_theme', 'dark');
local.setItem('embed_left_sidebar_collapsed', '1');
local.setItem('agent_view_mode', 'grid');

api.clearUserScopedStorage();
return { local: local.dump(), session: session.dump() };
"""
    )

    local = result["local"]
    session = result["session"]

    for key in (
        "user_info",
        "nav_snapshot_v1",
        "nanzi_mcp_recent_tokens",
        "workbench_pinned_agent_ids",
        "knowledge_portal_pinned",
        "knowledge_ragflow_metadata_top_k",
        "dataset_portal_keep_open",
        "nanzi.skillCreatedBanner.notified:skill-1",
    ):
        assert key not in local, f"登出后仍残留 {key}"

    for key in (
        "nzi_embed_personal_session_token",
        "nzi_embed_personal_session_token:inst-2",
        "dismiss_pwd_banner_7",
    ):
        assert key not in session, f"登出后仍残留 sessionStorage 键 {key}"

    assert local["nanzi_app_theme"] == "dark"
    assert local["embed_left_sidebar_collapsed"] == "1"
    assert local["agent_view_mode"] == "grid"
