"""权限归属：每个 element 只能挂在一个菜单下，且后端用到的码必须能在树上授出。

两个真实缺陷（扫描全树时发现）：

1. `element:metadata:import` 同时挂在「数据源管理」和「元数据管理」下，但数据源页面
   没有任何功能点权限、这个码只服务元数据页面。后果不是「显示重复」而已：勾「数据源
   管理」菜单会连带授予元数据导入权限，取消时又一起清掉。
2. 后端 `POST /api/portal/prompts/save` 要求 `element:prompts:edit`，但树里只有
   `element:prompts:optimize`——保存提示词的权限在界面上根本授不出去。

这里打真实 `MENU_TREE` 数据，并把后端源码里的权限码抓出来对账，防止以后再漏。
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
    script = f"""
(async () => {{
const ts = require('./frontend/node_modules/typescript');
const fs = require('fs');
const load = (file) => {{
  const source = fs.readFileSync(file, 'utf8');
  const code = ts.transpileModule(source, {{
    compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
  }}).outputText;
  const moduleRef = {{ exports: {{}} }};
  new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
  return moduleRef.exports;
}};
const api = Object.assign(
  {{}},
  load('frontend/src/constants/permissions.ts'),
  load('frontend/src/utils/permissionLists.ts'),
);
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


def _tree_element_ids() -> list[str]:
    return _run("""
const ids = [];
const walk = (nodes) => { for (const n of nodes || []) { if (n.id.startsWith('element:')) ids.push(n.id); walk(n.children || []); } };
walk(api.MENU_TREE);
return { ids }
""")["ids"]


def test_data_sources_menu_does_not_own_metadata_import():
    """「数据源管理」下不该挂元数据导入：该页面只有菜单级权限。"""
    result = _run("""
return { ids: api.getMenuDescendantIds('menu:data_sources') }
""")

    assert "element:metadata:import" not in result["ids"]


def test_toggling_data_sources_grants_only_the_menu_itself():
    """勾「数据源管理」只授出菜单，不会顺带授出任何功能点。"""
    result = _run("""
const menuId = 'menu:data_sources';
const out = api.applyMenuToggle([], [], menuId, api.getMenuDescendantIds(menuId), [], []);
return { menus: out.menus, elements: out.elements }
""")

    assert result["menus"] == ["menu:data_sources"]
    assert result["elements"] == []


def test_prompts_menu_exposes_save_permission():
    """「提示词工坊」要能授出「保存提示词」，否则后端那个端点对非 admin 永远 403。"""
    result = _run("""
return { ids: api.getMenuDescendantIds('menu:prompts') }
""")

    assert "element:prompts:edit" in result["ids"]
    assert "element:prompts:optimize" in result["ids"]


def test_every_element_belongs_to_exactly_one_menu():
    """同一 element 不能挂两个菜单，否则勾 A 菜单会连带授出 B 页面的权限。"""
    result = _run("""
const owners = new Map();
const walk = (nodes) => { for (const n of nodes || []) { if (n.id.startsWith('element:')) owners.set(n.id, (owners.get(n.id) || 0) + 1); walk(n.children || []); } };
walk(api.MENU_TREE);
return { duplicated: [...owners].filter(([, count]) => count > 1).map(([id]) => id) }
""")

    assert result["duplicated"] == [], f"这些功能点被挂了多次：{result['duplicated']}"


def test_backend_required_permissions_are_grantable_from_the_tree():
    """后端 `require_permission` 用到的每个码，都要能在权限树上勾出来。"""
    sources = list((ROOT / "app").rglob("*.py"))
    required: set[str] = set()
    pattern = re.compile(r'\(\s*"(menu|element)"\s*,\s*"(menu:[a-z0-9_:]+|element:[a-z0-9_:]+)"\s*\)')
    for path in sources:
        required.update(m.group(2) for m in pattern.finditer(path.read_text(encoding="utf-8", errors="ignore")))

    tree = set(_tree_element_ids())
    tree |= set(
        _run("""
const ids = [];
const walk = (nodes) => { for (const n of nodes || []) { if (n.id.startsWith('menu:')) ids.push(n.id); walk(n.children || []); } };
walk(api.MENU_TREE);
return { ids }
""")["ids"]
    )

    missing = sorted(required - tree)
    assert missing == [], f"后端要求但权限树里没有，界面上授不出去：{missing}"
