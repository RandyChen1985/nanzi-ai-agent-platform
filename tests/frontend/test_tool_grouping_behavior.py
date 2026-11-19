"""工具分组的唯一实现（`frontend/src/utils/toolGrouping.ts`）。

背景：智能体编辑页「工具能力」早就按能力域分组（ChatBI 数据分析 / 知识库检索 (RAG) /
联网搜索 / 浏览器自动化 / 系统自治工具 …），而权限页的「功能限定」弹框把工具全堆在一个
「内置工具」里。管理员在两个页面看到的结构对不上，也就无法对照着判断限定范围。

修法不是"在弹框里再写一套关键字规则"——那正是会漂移的做法。规则收进本模块，
智能体页与弹框共用，因此这里也钉一条"智能体页必须调用共享函数"的契约。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/toolGrouping.ts"
AGENT_MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"
pytestmark = pytest.mark.no_infrastructure

HARNESS = """
(async () => {
const fs = require('fs');
const path = require('path');
const ts = require('./frontend/node_modules/typescript');
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


def _run_typescript(expression: str):
    script = HARNESS.replace("__MODULE__", json.dumps(MODULE)).replace(
        "__EXPRESSION__", expression
    )
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_capability_keyword_rules_match_the_agent_page():
    """关键字规则逐条对齐智能体页改造前的行为（顺序有依赖，改动会直接改变分组）。"""
    result = _run_typescript(
        """
const names = [
  'read_file', 'edit_file', 'write_file', 'search_text', 'glob_files',
  'exec_command', 'list_process', 'manage_process', 'directory_tree_navigator',
  'code_syntax_linter', 'search_github_repos', 'read_image', 'generate_image',
  'browser_click', 'browser_navigate',
  'web_search', 'fetch_static_web_url', 'http_request',
  'query_dataset', 'chatbi_report',
  'knowledge_search', 'ragflow_query', 'document_read',
  'excel_document_write', 'word_document_read',
  'sub_agent_dispatch',
  'send_email', 'dingtalk_notify',
  'jira_issue', 'feishu_doc',
  'save_preference', 'memory_recall', 'unknown_thing', '',
];
const out = {};
names.forEach((name) => { out[name] = api.capabilityKeyOf(name); });
return out;
"""
    )

    assert result["read_file"] == "system"
    assert result["exec_command"] == "system"
    assert result["directory_tree_navigator"] == "system"
    assert result["code_syntax_linter"] == "system"
    # `search_github_repos` 走 `search_` 前缀 → system（不是 knowledge/web）
    assert result["search_github_repos"] == "system"
    assert result["read_image"] == "system", "read_ 前缀 → 系统自治工具"
    # 出图工具靠 'image' 关键字进 system：它不以 read_/write_ 等前缀开头，
    # 漏了这条就会掉进「其他扩展工具」兜底组，和 read_image 分家。
    assert result["generate_image"] == "system", "generate_image 应与 read_image 同组"
    assert result["browser_click"] == "browser"
    assert result["browser_navigate"] == "browser"
    assert result["web_search"] == "web"
    assert result["fetch_static_web_url"] == "web"
    assert result["query_dataset"] == "chatbi"
    assert result["knowledge_search"] == "knowledge"
    assert result["ragflow_query"] == "knowledge"
    assert result["excel_document_write"] == "office"
    assert result["sub_agent_dispatch"] == "delegation"
    assert result["send_email"] == "notification"
    assert result["jira_issue"] == "office"
    assert result["save_preference"] == "memory"
    assert result["unknown_thing"] == "other"
    assert result[""] == "other", "空名字不得抛错"


def test_display_groups_follow_agent_page_ordering():
    """展示顺序：能力域（固定顺序）→ 业务分组（按数量降序）→ 兜底组殿后。"""
    result = _run_typescript(
        """
const tools = [
  { name: 'read_file' },
  { name: 'exec_command' },
  { name: 'browser_click' },
  { name: 'web_search' },
  { name: 'crm_sync', group_name: '客户域' },
  { name: 'erp_sync', group_name: '客户域' },
  { name: 'billing_sync', group_name: '计费域' },
  { name: 'mystery_tool' },
];
const groups = api.groupToolsForDisplay(
  tools,
  (tool) => tool.name,
  (tool) => tool.group_name,
);
return groups.map((group) => ({
  key: group.key,
  label: group.label,
  kind: group.kind,
  count: group.items.length,
}));
"""
    )

    labels = [group["label"] for group in result]
    assert labels == [
        "联网搜索",
        "浏览器自动化",
        "系统自治工具",
        "客户域",
        "计费域",
        "其他扩展工具",
    ], "能力域按固定顺序，业务分组按工具数降序，兜底组最后"
    kinds = {group["label"]: group["kind"] for group in result}
    assert kinds["系统自治工具"] == "capability"
    assert kinds["客户域"] == "business"
    assert kinds["其他扩展工具"] == "fallback"


def test_business_group_wins_over_keyword_guessing():
    """显式业务分组优先于关键字猜测：API 工具叫 read_xxx 也不该落进「系统自治工具」。"""
    result = _run_typescript(
        """
const groups = api.groupToolsForDisplay(
  [{ name: 'read_customer_record', group_name: '客户域' }],
  (tool) => tool.name,
  (tool) => tool.group_name,
);
return groups.map((group) => group.label);
"""
    )

    assert result == ["客户域"], "有 group_name 就必须归到业务域，不被 read_ 前缀截胡"


def test_empty_and_blank_inputs_are_tolerated():
    result = _run_typescript(
        """
return {
  empty: api.groupToolsForDisplay([], (item) => item.name).length,
  nullish: api.groupToolsForDisplay(null, (item) => item.name).length,
  blankGroup: api.groupToolsForDisplay(
    [{ name: 'plain_tool', group_name: '   ' }],
    (item) => item.name,
    (item) => item.group_name,
  ).map((group) => group.label),
};
"""
    )

    assert result["empty"] == 0
    assert result["nullish"] == 0
    assert result["blankGroup"] == ["其他扩展工具"], "空白业务分组按未分组处理"


def test_agent_page_reuses_the_shared_grouping():
    """防漂移契约：智能体页不得再内联一份关键字规则。

    一旦两处各写一套，「同一个工具在两个页面归属不同组」就会重新出现，
    而这正是本次要修的问题本身。
    """
    source = AGENT_MANAGEMENT.read_text(encoding="utf-8")

    assert "groupToolsForDisplay" in source, "智能体页必须调用共享分组函数"
    assert "from '../utils/toolGrouping'" in source or 'from "../utils/toolGrouping"' in source
    # 内联的关键字规则若还在，说明只是"又抄了一份"
    assert "name.includes('system_command')" not in source, (
        "关键字规则只能有一份，智能体页里不该再出现内联版本"
    )
    assert "const businessGroups = new Map<string, ToolGroup>()" not in source


def test_restriction_dialog_regroups_tools_like_the_agent_page():
    """权限弹框的工具页签必须按同一规则重新聚合。

    接口只给「内置工具 / 自定义 API 工具」两桶，而智能体编辑页展示的是能力域
    （ChatBI 数据分析、系统自治工具 …）。不重新聚合，两个页面的结构就对不上，
    管理员无法对照着判断限定范围——这正是本次要修的问题。
    """
    dialog = (ROOT / "frontend/src/components/system/AgentRestrictionDialog.vue").read_text(
        encoding="utf-8"
    )
    assert "regroupToolGroups" in dialog, "工具页签必须走共享聚合函数"
    assert "tab.key === 'tools' ? regroupToolGroups(tab.groups) : tab.groups" in dialog, (
        "只对工具页签重新聚合：MCP 按 server 分组、技能按公共/个人分组，都不要动"
    )

    logic = (ROOT / "frontend/src/utils/agentRestrictions.ts").read_text(encoding="utf-8")
    assert "export function regroupToolGroups(" in logic
    # 组键必须仍被判为"工具"，否则选中项会被写进技能那一列
    assert "tools:" in (ROOT / "frontend/src/utils/toolGrouping.ts").read_text(encoding="utf-8")
    assert "endsWith('_skills')" in logic, "归类依据仍是 _skills 后缀，新组键不带该后缀"
