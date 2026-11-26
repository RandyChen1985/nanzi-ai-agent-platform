"""禁用工具分组的纯函数行为（spec §4 的第 6～9 项）。

这些函数是组件唯一的聚合/计数/全选/过滤来源，所以直接在 node harness 里
跑真实 TypeScript 实现，不挂载组件——组件只管渲染与折叠状态。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/forbiddenToolGroups.ts"
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


# 顺序即后端返回顺序：两个 builtin 组相邻、mcp/api 在后——用来验证"聚合保序"。
CANDIDATES = [
    {
        "id": "exec_command",
        "label": "执行终端命令",
        "description": "在沙箱中执行 Bash/shell 命令行",
        "category": "sandbox",
        "aliases": ["exec_command"],
        "group_key": "builtin:sandbox",
        "group": "沙箱命令",
    },
    {
        "id": "write_file",
        "label": "写入/修改文件",
        "description": "写入或修改文件内容",
        "category": "file",
        "aliases": ["write_file", "edit_file", "apply_patch"],
        "group_key": "builtin:file",
        "group": "文件操作",
    },
    {
        "id": "read_file",
        "label": "读取文件",
        "description": "读取文件内容",
        "category": "file",
        "aliases": ["read_file"],
        "group_key": "builtin:file",
        "group": "文件操作",
    },
    {
        "id": "jira_search",
        "label": "Jira 搜索",
        "description": "使用 JQL 语法查询 Jira 历史工单",
        "category": "mcp",
        "aliases": ["jira_search"],
        "group_key": "mcp:jira",
        "group": "MCP · jira",
    },
    {
        "id": "send_email",
        "label": "发送邮件",
        "description": "向指定收件人发送邮件",
        "category": "api",
        "aliases": ["send_email"],
        "group_key": "api:履约罚款",
        "group": "自定义 API · 履约罚款",
    },
]


def test_group_candidates_aggregates_in_first_seen_order():
    """按 group_key 聚合、子组与组内候选都保序、不产生空组；缺 group_key 时兜底不丢候选。"""
    result = _run_typescript(
        f"""
const candidates = {json.dumps(CANDIDATES)};
const groups = api.groupCandidates(candidates);
const legacy = api.groupCandidates([
  {{ id: 'legacy_tool', label: 'legacy_tool', description: '', category: 'builtin', aliases: ['legacy_tool'] }},
]);
return {{
  keys: groups.map((group) => group.key),
  labels: groups.map((group) => group.label),
  ids: groups.map((group) => group.candidates.map((candidate) => candidate.id)),
  emptyInput: api.groupCandidates([]),
  legacyKeys: legacy.map((group) => group.key),
  legacyIds: legacy.map((group) => group.candidates.map((candidate) => candidate.id)),
}};
"""
    )
    assert result["keys"] == [
        "builtin:sandbox",
        "builtin:file",
        "mcp:jira",
        "api:履约罚款",
    ]
    assert result["labels"] == ["沙箱命令", "文件操作", "MCP · jira", "自定义 API · 履约罚款"]
    # 同组候选落在一组，且组内保持后端返回顺序
    assert result["ids"][1] == ["write_file", "read_file"]
    assert result["emptyInput"] == []
    assert all(group_ids for group_ids in result["ids"]), "不得产生空组"
    assert result["legacyKeys"] == ["__ungrouped__"], "缺 group_key 时归入兜底组而不是丢候选"
    assert result["legacyIds"] == [["legacy_tool"]]


def test_group_selected_count_counts_candidates_not_aliases():
    """计数按 aliases 命中算：勾 write_file 只算 1 个候选，即使它带 3 个别名。"""
    result = _run_typescript(
        f"""
const groups = api.groupCandidates({json.dumps(CANDIDATES)});
const fileGroup = groups.find((group) => group.key === 'builtin:file');
return {{
  oneAlias: api.groupSelectedCount(fileGroup, new Set(['edit_file'])),
  wholeGroup: api.groupSelectedCount(fileGroup, new Set(['write_file', 'edit_file', 'apply_patch', 'read_file'])),
  outsideGroup: api.groupSelectedCount(fileGroup, new Set(['exec_command'])),
  arrayForm: api.groupSelectedCount(fileGroup, ['read_file']),
}};
"""
    )
    assert result["oneAlias"] == 1, "一个候选命中多个别名也只算 1"
    assert result["wholeGroup"] == 2, "组内 2 个候选全选中"
    assert result["outsideGroup"] == 0
    assert result["arrayForm"] == 1, "也应接受数组形式的选中集合"


def test_apply_group_selection_union_diff_and_immutability():
    """勾选=并集、取消=差集；保序去重、不修改入参、不触碰组外项（如 orphan）。"""
    result = _run_typescript(
        f"""
const candidates = {json.dumps(CANDIDATES)};
const fileCandidates = candidates.filter((candidate) => candidate.group_key === 'builtin:file');
const before = ['orphan_tool', 'write_file', 'exec_command'];
const snapshot = JSON.stringify(before);
const checked = api.applyGroupSelection(before, fileCandidates, true);
const unchecked = api.applyGroupSelection(checked, fileCandidates, false);
const deduped = api.applyGroupSelection(['write_file', 'write_file'], fileCandidates, true);
return {{
  checked,
  unchecked,
  deduped,
  inputUntouched: JSON.stringify(before) === snapshot,
  returnedNewArray: checked !== before,
}};
"""
    )
    assert result["checked"] == [
        "orphan_tool",
        "write_file",
        "exec_command",
        "edit_file",
        "apply_patch",
        "read_file",
    ], "原有项保持原序，新增别名按候选顺序追加"
    assert result["unchecked"] == ["orphan_tool", "exec_command"], "取消只移除组内别名"
    assert result["deduped"] == ["write_file", "edit_file", "apply_patch", "read_file"]
    assert result["inputUntouched"] is True
    assert result["returnedNewArray"] is True


def test_filter_sub_groups_matches_any_field_and_returns_input_when_empty():
    """group_key / group / id / label / description 任一命中即保留该子组；空查询原样返回。"""
    result = _run_typescript(
        f"""
const groups = api.groupCandidates({json.dumps(CANDIDATES)});
const keys = (query) => api.filterSubGroups(groups, query).map((group) => group.key);
return {{
  byGroupKey: keys('mcp:'),
  byGroupLabel: keys('履约罚款'),
  byCandidateId: keys('read_file'),
  byCandidateLabel: keys('发送邮件'),
  byDescription: keys('沙箱'),
  caseInsensitive: keys('JIRA_SEARCH'),
  blank: keys('   '),
  noMatch: keys('不存在的工具'),
  allKeys: groups.map((group) => group.key),
}};
"""
    )
    assert result["byGroupKey"] == ["mcp:jira"]
    assert result["byGroupLabel"] == ["api:履约罚款"]
    assert result["byCandidateId"] == ["builtin:file"]
    assert result["byCandidateLabel"] == ["api:履约罚款"]
    assert result["byDescription"] == ["builtin:sandbox"]
    assert result["caseInsensitive"] == ["mcp:jira"]
    assert result["blank"] == result["allKeys"], "空查询原样返回"
    assert result["noMatch"] == []


def test_find_orphan_ids_keeps_aliases_of_known_candidates_out_of_the_history_area():
    """缺陷 A 回归：勾选一个能力会写入整组别名（write_file → edit_file/apply_patch），
    这些别名不属于任何候选的 id，但**不是**「历史遗留」——它们必须留在原分组里继续显示，
    只有"目录里完全查不到"的名字才进历史区。"""
    result = _run_typescript(
        f"""
const candidates = {json.dumps(CANDIDATES)};
return {{
  aliasOnly: api.findOrphanIds(['write_file', 'edit_file', 'apply_patch'], candidates),
  realOrphan: api.findOrphanIds(['write_file', 'edit_file', 'removed_mcp_tool'], candidates),
  emptyCatalog: api.findOrphanIds(['write_file'], []),
}};
"""
    )

    assert result["aliasOnly"] == [], "别名被误判成历史遗留项（截图里 Write/Edit 就是这么来的）"
    assert result["realOrphan"] == ["removed_mcp_tool"]
    # 目录还没加载出来（或加载失败）时不能吞掉已配置项，否则用户无法解除
    assert result["emptyCatalog"] == ["write_file"]


def test_group_tone_turns_red_only_when_the_group_has_a_selection():
    """有选中 = 整卡红化；无选中沿用各组基色（内置组灰、API/MCP 组靛蓝）。"""
    result = _run_typescript(
        """
return {
  selected: api.groupToneClasses(true, 'gray'),
  selectedIndigo: api.groupToneClasses(true, 'indigo'),
  plainGray: api.groupToneClasses(false, 'gray'),
  plainIndigo: api.groupToneClasses(false, 'indigo'),
};
"""
    )

    # 有选中：大组卡片、子分组卡片、子分组头部、标题、计数、箭头、圆点、全选按钮全部转红
    for tone in (result["selected"], result["selectedIndigo"]):
        assert "border-red-200" in tone["bigCard"]
        assert "bg-red-50/40" in tone["bigCard"]
        assert "border-red-200" in tone["subCard"]
        assert "bg-red-50" in tone["subHead"]
        assert tone["title"] == "text-red-700"
        assert tone["count"] == "text-red-500"
        assert tone["arrow"] == "text-red-400"
        assert tone["dot"] == "bg-red-500"
        assert "text-red-600" in tone["action"]

    # 两种基色在有选中时收敛到同一套红，避免"组不同则红不同"
    assert result["selected"] == result["selectedIndigo"]

    # 无选中：保持现状，不得残留红色标题/底色
    assert "border-gray-100" in result["plainGray"]["bigCard"]
    assert "bg-white" in result["plainGray"]["bigCard"]
    assert result["plainGray"]["subCard"] == "border-gray-200"
    assert "bg-gray-50" in result["plainGray"]["subHead"]
    assert result["plainGray"]["title"] == "text-gray-700"
    assert result["plainGray"]["count"] == "text-gray-400"
    # 内置组圆点本来就是红色（沙箱/高危语义），这里不能被"无选中"改掉
    assert result["plainGray"]["dot"] == "bg-red-500"

    assert result["plainIndigo"]["subCard"] == "border-primary/10"
    assert "bg-primary/5" in result["plainIndigo"]["subHead"]
    assert result["plainIndigo"]["title"] == "text-primary-dark"
    assert result["plainIndigo"]["count"] == "text-primary"
    assert result["plainIndigo"]["arrow"] == "text-primary"
    assert result["plainIndigo"]["dot"] == "bg-primary"
    assert "text-primary" in result["plainIndigo"]["action"]


# skills 模式：后端 `skill_candidates` 只给 { id, label, description, group_key, group }，没有 category/aliases。
# 目录里列了两组，用来钉住"聚合不得写死只有一个组"（当前后端只发 skill:__all__）。
SKILL_CANDIDATES = [
    {
        "id": "skill:report-export",
        "label": "报表导出",
        "description": "把会话内容导出为报表文件",
        "group_key": "skill:__all__",
        "group": "公共技能",
    },
    {
        "id": "skill:web-search",
        "label": "联网搜索",
        "description": "调用搜索引擎检索资料",
        "group_key": "skill:__all__",
        "group": "公共技能",
    },
    {
        "id": "skill:sql-audit",
        "label": "SQL 审计",
        "description": "审查 SQL 合规性",
        "group_key": "skill:audit",
        "group": "公共技能 · 审计",
    },
]


def test_skill_candidates_without_aliases_are_grouped_and_selected_by_id():
    """技能候选只有 `id`（没有别名概念），聚合与选中判定必须退化为按 id。

    否则 `(n/m)` 恒为 0、组卡片永远不变红、全选一个 id 都写不进去——skills 模式整块失效。
    这两条同时是"skills 复用同一套纯函数、不另写一份技能逻辑"的行为证据。
    """
    result = _run_typescript(
        f"""
const candidates = {json.dumps(SKILL_CANDIDATES)};
const groups = api.groupCandidates(candidates);
const allGroup = groups.find((group) => group.key === 'skill:__all__');
return {{
  groupKeys: groups.map((group) => group.key),
  groupLabels: groups.map((group) => group.label),
  groupIds: groups.map((group) => group.candidates.map((candidate) => candidate.id)),
  countOne: api.groupSelectedCount(allGroup, new Set(['skill:web-search'])),
  countAll: api.groupSelectedCount(allGroup, new Set(['skill:report-export', 'skill:web-search'])),
  countOneOfTwo: api.groupSelectedCount(allGroup, new Set(['skill:report-export'])),
  emptyAliases: api.groupSelectedCount(
    {{ key: 'g', label: 'g', candidates: [{{ id: 'skill:empty', aliases: [] }}] }},
    new Set(['skill:empty']),
  ),
  checked: api.applyGroupSelection(['legacy:skill'], allGroup.candidates, true),
  unchecked: api.applyGroupSelection(
    ['legacy:skill', 'skill:report-export', 'skill:web-search'],
    allGroup.candidates,
    false,
  ),
  filteredByLabel: api.filterSubGroups(groups, '审计').map((group) => group.key),
  filteredById: api.filterSubGroups(groups, 'WEB-SEARCH').map((group) => group.key),
  orphans: api.findOrphanIds(['skill:report-export', 'skill:removed'], candidates),
}};
"""
    )

    # 聚合沿用同一个 groupCandidates：保序、不写死"只有一个组"
    assert result["groupKeys"] == ["skill:__all__", "skill:audit"]
    assert result["groupLabels"] == ["公共技能", "公共技能 · 审计"]
    assert result["groupIds"][0] == ["skill:report-export", "skill:web-search"]
    assert all(group_ids for group_ids in result["groupIds"]), "不得产生空组"

    # 计数：按 id 命中，一个技能算 1
    assert result["countOne"] == 1
    assert result["countOneOfTwo"] == 1
    assert result["countAll"] == 2
    assert result["emptyAliases"] == 1, "aliases 显式为空时同样退化为按 id"

    # 全选写入整组 id、取消全选只移除组内 id（不碰组外项）
    assert result["checked"] == ["legacy:skill", "skill:report-export", "skill:web-search"]
    assert result["unchecked"] == ["legacy:skill"]

    # 搜索与 orphan 判定同样复用，不需要技能专用分支
    assert result["filteredByLabel"] == ["skill:audit"]
    assert result["filteredById"] == ["skill:__all__"]
    assert result["orphans"] == ["skill:removed"], "已下架的技能仍要能解除禁用"


def test_tool_tab_split_matches_the_agent_dialog_rule():
    """工具/MCP 分流必须与「功能限定」弹框同源，否则同一资源在两个入口归类不一致。

    后端 `group_key` 的稳定格式是 `builtin:<cat>` / `api:<group>` / `mcp:<server>`，
    所以判据只需要看前缀，不必依赖 `category` 字段（技能候选就没有 category）。
    """
    cases = _run_typescript("""
return [
  ['builtin:execution', api.toolTabOfCandidate({ group_key: 'builtin:execution' })],
  ['api:__ungrouped__', api.toolTabOfCandidate({ group_key: 'api:__ungrouped__' })],
  ['mcp:nanzi-echo', api.toolTabOfCandidate({ group_key: 'mcp:nanzi-echo' })],
  ['MCP:upper', api.toolTabOfCandidate({ group_key: 'MCP:upper' })],
  ['(no key)', api.toolTabOfCandidate({})],
  ['(null)', api.toolTabOfCandidate(null)],
  ['(legacy mcp)', api.toolTabOfCandidate({ category: 'mcp' })],
  ['(legacy api)', api.toolTabOfCandidate({ category: 'api' })],
];
""")
    got = {name: tab for name, tab in cases}
    assert got["builtin:execution"] == "tools"
    assert got["api:__ungrouped__"] == "tools", "自定义 API 归工具页（与内置工具同为平台内工具）"
    assert got["mcp:nanzi-echo"] == "mcp"
    assert got["MCP:upper"] == "mcp", "大小写不敏感"
    # 兜底：没有 group_key 的候选归工具（后端应始终给值，这是防御而非契约）
    assert got["(no key)"] == "tools"
    assert got["(null)"] == "tools"
    # 旧响应没有 group_key 时退回 category，否则升级瞬间 MCP 工具会集体跑到工具页
    assert got["(legacy mcp)"] == "mcp"
    assert got["(legacy api)"] == "tools"


def test_filter_by_tool_tab_keeps_order_and_does_not_drop_unknown():
    """分流必须无遗漏、无重复：漏掉的候选在界面上直接消失，用户会以为资源不存在。"""
    split = _run_typescript("""
const candidates = [
  { id: 'a', group_key: 'builtin:execution' },
  { id: 'b', group_key: 'mcp:server-a' },
  { id: 'c', group_key: 'api:通知' },
  { id: 'd', group_key: 'mcp:server-b' },
];
return {
  tools: api.filterCandidatesByToolTab(candidates, 'tools').map(c => c.id),
  mcp: api.filterCandidatesByToolTab(candidates, 'mcp').map(c => c.id),
  total: candidates.length,
};
""")
    assert split["tools"] == ["a", "c"], "工具页含内置与自定义 API，且保持原序"
    assert split["mcp"] == ["b", "d"], "MCP 页只含 MCP，且保持原序"
    assert len(split["tools"]) + len(split["mcp"]) == split["total"], "分流无遗漏无重复"
