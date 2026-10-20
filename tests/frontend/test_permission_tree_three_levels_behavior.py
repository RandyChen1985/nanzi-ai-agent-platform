"""三层权限树的行为测试：真实 MENU_TREE 数据下的后代 id、勾选语义与三态判定。

为什么单独打一层：`getMenuDescendantIds()` 过去只取直接子项，三层之后「全选用户管理」
必须能覆盖到它自己的 4 个功能点。2026-10-20 起「勾菜单」与「全选」拆成两个动作，
这里同时钉住：勾入口只收菜单、点「全选」才级联、只授入口不算整棵子树全选。
这里直接加载真实的 `constants/permissions.ts`，不复制一份树。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
    """加载 constants + permissionLists 两个模块，执行一段表达式。"""
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
const result = await (async () => {{ {expression} }})();
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


def test_submenu_descendants_include_its_own_elements_only():
    """「用户管理」的后代 = 它的 4 个功能点，不含兄弟页面。"""
    result = _run("""
const ids = api.getMenuDescendantIds('menu:system:users');
return { ids, sorted: [...ids].sort() }
""")

    assert result["ids"] == [
        "element:user:view_key",
        "element:user:reset_key",
        "element:user:edit",
        "element:user:delete",
    ]
    assert "menu:system:roles" not in result["ids"]
    assert "element:role:edit" not in result["ids"]


def test_menu_descendants_cover_both_submenus_and_their_elements():
    """「系统管理」的后代要同时覆盖 4 个子菜单与 8 个功能点。"""
    result = _run("""
const ids = api.getMenuDescendantIds('menu:system');
return { count: ids.length, submenus: ids.filter(id => id.startsWith('menu:')), elements: ids.filter(id => id.startsWith('element:')) }
""")

    assert result["count"] == 12, result
    assert sorted(result["submenus"]) == [
        "menu:system:audit",
        "menu:system:config",
        "menu:system:roles",
        "menu:system:users",
    ]
    assert len(result["elements"]) == 8


def test_flat_menu_descendants_are_unchanged():
    """没有子菜单的菜单（如元数据管理）行为不变：直接是它的功能点。"""
    result = _run("""
return { ids: api.getMenuDescendantIds('menu:metadata'), leafCount: api.getMenuDescendantIds('menu:metadata').length }
""")

    assert result["leafCount"] == 7
    assert all(not i.startswith("menu:") for i in result["ids"])


def test_entry_toggle_grants_only_the_submenu_itself():
    """勾「用户管理」只授予页面入口，不再自动勾它的功能点（解耦）。"""
    result = _run("""
const descendants = api.getMenuDescendantIds('menu:system:users');
const out = api.applyMenuEntryToggle([], [], 'menu:system:users', descendants);
return { menus: out.menus, elements: out.elements }
""")

    assert result["menus"] == ["menu:system:users"]
    assert result["elements"] == [], "功能点必须单独勾选，旧版「勾页面即全选」会让「只给入口」无法表达"


def test_cascading_submenu_grants_its_elements_but_not_siblings():
    """「全选」用户管理 → menus 收自己、elements 收它的功能点；「角色管理」不受影响。"""
    result = _run("""
const descendants = api.getMenuDescendantIds('menu:system:users');
const out = api.applyMenuCascadeToggle([], [], 'menu:system:users', descendants, 'select');
return { menus: out.menus, elements: out.elements }
""")

    assert result["menus"] == ["menu:system:users"]
    assert sorted(result["elements"]) == [
        "element:user:delete",
        "element:user:edit",
        "element:user:reset_key",
        "element:user:view_key",
    ]
    assert "element:role:edit" not in result["elements"]


def test_submenu_is_fully_granted_only_when_entry_and_all_elements_are_granted():
    """「全选」判定 = 自己已授予 且 全部功能点已授予；只授入口不算全选。"""
    result = _run("""
const node = { id: 'menu:system:users', label: '用户管理', children: [
  { id: 'element:user:view_key', label: '查看' },
  { id: 'element:user:edit', label: '编辑' },
]};
const sets = (menus, elements, inheritedMenus = [], inheritedElements = []) => ({ menus, elements, inheritedMenus, inheritedElements });
return {
  fullGranted: api.isPermissionNodeGranted(node, sets(['menu:system:users'], ['element:user:view_key', 'element:user:edit'])),
  entryOnly: api.isPermissionNodeGranted(node, sets(['menu:system:users'], [])),
  selfGrantedEntryOnly: api.isPermissionNodeSelfGranted(node, sets(['menu:system:users'], [])),
  partial: api.isPermissionNodePartiallySelected(node, sets([], ['element:user:view_key'])),
  noneGranted: api.isPermissionNodeGranted(node, sets([], [])),
  inheritedAll: api.isPermissionNodeGranted(node, sets([], [], [], ['element:user:view_key', 'element:user:edit'])),
  inheritedWithEntry: api.isPermissionNodeGranted(node, sets(['menu:system:users'], [], [], ['element:user:view_key', 'element:user:edit'])),
}
""")

    assert result["fullGranted"] is True
    assert result["entryOnly"] is False, "只勾入口时整棵子树不是全选"
    assert result["selfGrantedEntryOnly"] is True, "但入口自身是勾选态"
    assert result["partial"] is True, "入口未勾、部分功能点已授予 → 半选"
    assert result["noneGranted"] is False
    assert result["inheritedAll"] is False, "功能点全继承但入口没给，仍不算全选"
    assert result["inheritedWithEntry"] is True


def test_leaf_element_count_skips_submenu_nodes():
    """卡片上的「N 个功能点」只数叶子，不把子菜单算进去。"""
    result = _run("""
return {
  system: api.countLeafElements({ id: 'menu:system', label: '系统管理', children: [
    { id: 'menu:system:users', label: '用户管理', children: [
      { id: 'element:user:view_key', label: 'a' },
      { id: 'element:user:edit', label: 'b' },
    ]},
    { id: 'element:system:config_save', label: 'c' },
  ]}),
  empty: api.countLeafElements({ id: 'menu:dashboard', label: '概览', children: [] }),
}
""")

    assert result["system"] == 3
    assert result["empty"] == 0
