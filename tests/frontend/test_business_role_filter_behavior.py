"""业务角色搜索：name 与 code 都要能搜到。

用户反馈（admin 身份）：创建用户 → 业务角色里输入 `de`，列表空白，选不到 `default`。
角色管理页能搜到 `default`（后端 `search` 同时匹配 name 与 code），但用户弹窗的过滤
只看 `role.name`——老库里这个角色的 name 往往是「默认用户」之类，只有 code 是 `default`，
于是搜 `de` 命中 code、却匹配不到 name，列表就空了。

这里把过滤收成纯函数，name / code 任一命中即保留，且容忍 null 字段与前后空格。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/roleFilter.ts"
pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
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
const result = await (async () => {{ return await (async () => {{ {expression} }})(); }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_matches_code_when_name_is_a_localized_label():
    """老库：code=default、name=默认用户。搜 de 命中 code，必须能搜到。"""
    result = _run("""
const roles = [
  { id: 1, code: 'default', name: '默认用户' },
  { id: 2, code: 'analyst', name: '数据分析师' },
];
return { ids: api.filterBusinessRoles(roles, 'de').map(r => r.id) }
""")

    assert result["ids"] == [1]


def test_search_is_case_insensitive_and_trimmed():
    """大小写与前后的空格都不该让人搜不到。"""
    result = _run("""
const roles = [{ id: 1, code: 'default', name: '默认用户' }];
return {
  upper: api.filterBusinessRoles(roles, 'DE').map(r => r.id),
  trimmed: api.filterBusinessRoles(roles, '  de  ').map(r => r.id),
}
""")

    assert result["upper"] == [1]
    assert result["trimmed"] == [1]


def test_still_matches_name():
    """按中文名搜索的原有用法不能丢。"""
    result = _run("""
const roles = [{ id: 2, code: 'analyst', name: '数据分析师' }];
return { ids: api.filterBusinessRoles(roles, '数据').map(r => r.id) }
""")

    assert result["ids"] == [2]


def test_empty_keyword_returns_everything():
    """没输入时展示全部（含只有空格的情况）。"""
    result = _run("""
const roles = [{ id: 1, code: 'a', name: '甲' }, { id: 2, code: 'b', name: '乙' }];
return {
  empty: api.filterBusinessRoles(roles, '').map(r => r.id),
  blank: api.filterBusinessRoles(roles, '   ').map(r => r.id),
}
""")

    assert result["empty"] == [1, 2]
    assert result["blank"] == [1, 2]


def test_tolerates_missing_name_or_code():
    """脏数据（name/code 为 null）不该让整个下拉抛错白屏。"""
    result = _run("""
const roles = [
  { id: 1, code: 'default', name: null },
  { id: 2, code: null, name: '只有名字' },
  { id: 3 },
];
return {
  byCode: api.filterBusinessRoles(roles, 'default').map(r => r.id),
  byName: api.filterBusinessRoles(roles, '只有').map(r => r.id),
  all: api.filterBusinessRoles(roles, '').map(r => r.id),
}
""")

    assert result["byCode"] == [1]
    assert result["byName"] == [2]
    assert result["all"] == [1, 2, 3]


def test_no_match_returns_empty_list():
    """确实没有匹配时返回空数组，由界面显示「没有匹配」提示。"""
    result = _run("""
const roles = [{ id: 1, code: 'default', name: '默认用户' }];
return { ids: api.filterBusinessRoles(roles, 'zzz').map(r => r.id) }
""")

    assert result["ids"] == []


def test_selected_summary_counts_roles_outside_current_result():
    """搜索只回一段结果时，已勾选但不在列表里的角色要能被计数出来。

    编辑用户时，用户的角色可能不在当前搜索结果里（默认按创建时间倒序只回前 100 条），
    界面若只显示网格，会让人以为「这个用户没有角色」。
    """
    result = _run("""
const roles = [{ id: 1, code: 'a', name: '甲' }];
return api.summarizeSelectedRoles([1, 2, 3], roles)
""")

    assert result == {"selectedCount": 3, "missingCount": 2}


def test_selected_summary_handles_empty_and_dirty_input():
    """没有已选项、或返回 null 时不能抛错。"""
    result = _run("""
return {
  empty: api.summarizeSelectedRoles([], []),
  nulls: api.summarizeSelectedRoles(null, null),
}
""")

    assert result["empty"] == {"selectedCount": 0, "missingCount": 0}
    assert result["nulls"] == {"selectedCount": 0, "missingCount": 0}
