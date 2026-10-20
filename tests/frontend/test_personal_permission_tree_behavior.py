"""「我的权限 → 平台功能权限」只读树的数据构建行为。

权限清单展示的是**用户实际拥有**的权限，而 MENU_TREE 是全量菜单定义。构建函数负责
「取交集并保留层级」，这里逐条钉住四类容易写错的边界：

1. 自身与后代都没授权 → 整支剪掉（不能把全量树漏出来）；
2. 自身未授权、后代有授权 → 保留为 `granted: false` 的容器（只授子页面是合法组合）；
3. 只授菜单不授功能点 → 菜单在、功能点不在，计数为 0；
4. 后端新增、前端树还没同步的权限码 → 走 unmatched 返回，绝不静默丢项。

纯函数测试，不依赖 Vue；渲染契约见 test_personal_permissions_tree_contract.py。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/permissionTreeView.ts"

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


TREE_FIXTURE = """
const tree = [
  {
    id: 'menu:a',
    label: '甲菜单',
    children: [
      { id: 'element:a1', label: '甲功能一' },
      { id: 'element:a2', label: '甲功能二' },
    ],
  },
  {
    id: 'menu:b',
    label: '乙菜单',
    children: [
      {
        id: 'menu:b1',
        label: '乙页面',
        children: [
          { id: 'element:b11', label: '乙功能一' },
          { id: 'element:b12', label: '乙功能二' },
        ],
      },
    ],
  },
  { id: 'menu:c', label: '丙菜单', children: [] },
];
"""


def test_prunes_branches_without_any_grant():
    """全量树不能漏出来：没有任何授权的分支整支消失。"""
    result = _run(
        TREE_FIXTURE
        + """
const view = api.buildGrantedPermissionView(tree, ['menu:a'], ['element:a1']);
return {
  ids: view.nodes.map(node => node.id),
  childIds: view.nodes[0].children.map(node => node.id),
};
"""
    )

    assert result["ids"] == ["menu:a"]
    assert result["childIds"] == ["element:a1"]


def test_ungranted_parent_is_kept_only_as_container():
    """只授子页面时，父级以 `granted: false` 的容器身份出现，不能冒充权限。"""
    result = _run(
        TREE_FIXTURE
        + """
const view = api.buildGrantedPermissionView(tree, ['menu:b1'], []);
const parent = view.nodes.find(node => node.id === 'menu:b');
return {
  parentGranted: parent.granted,
  parentKind: parent.kind,
  childIds: parent.children.map(node => node.id),
  childGranted: parent.children[0].granted,
};
"""
    )

    assert result["parentGranted"] is False
    assert result["parentKind"] == "menu"
    assert result["childIds"] == ["menu:b1"]
    assert result["childGranted"] is True


def test_menu_granted_without_elements_has_zero_count():
    """只给入口不给功能点是合法组合：菜单保留，计数为 0，子节点为空。"""
    result = _run(
        TREE_FIXTURE
        + """
const view = api.buildGrantedPermissionView(tree, ['menu:c'], []);
const node = view.nodes.find(item => item.id === 'menu:c');
return { granted: node.granted, count: node.grantedElementCount, children: node.children.length };
"""
    )

    assert result == {"granted": True, "count": 0, "children": 0}


def test_element_count_sums_whole_subtree():
    """菜单的「功能点 n」要统计整棵子树，不能只看直接子节点。"""
    result = _run(
        TREE_FIXTURE
        + """
const view = api.buildGrantedPermissionView(
  tree,
  ['menu:b', 'menu:b1'],
  ['element:b11', 'element:b12'],
);
const parent = view.nodes.find(node => node.id === 'menu:b');
return {
  parentCount: parent.grantedElementCount,
  pageCount: parent.children[0].grantedElementCount,
};
"""
    )

    assert result["parentCount"] == 2
    assert result["pageCount"] == 2


def test_unknown_ids_are_returned_not_dropped():
    """后端新增权限码、前端树未同步时，必须原样交出 id 让调用方兜底展示。"""
    result = _run(
        TREE_FIXTURE
        + """
const view = api.buildGrantedPermissionView(
  tree,
  ['menu:a', 'menu:brand_new'],
  ['element:new:do'],
);
return {
  nodes: view.nodes.map(node => node.id),
  unmatchedMenus: view.unmatchedMenus,
  unmatchedElements: view.unmatchedElements,
};
"""
    )

    assert result["nodes"] == ["menu:a"]
    assert result["unmatchedMenus"] == ["menu:brand_new"]
    assert result["unmatchedElements"] == ["element:new:do"]


def test_inputs_are_not_mutated_and_empty_grants_yield_empty_view():
    """纯函数：不改入参；空授权返回空树与空 unmatched。"""
    result = _run(
        TREE_FIXTURE
        + """
const menus = ['menu:a'];
const elements = ['element:a1'];
api.buildGrantedPermissionView(tree, menus, elements);
const empty = api.buildGrantedPermissionView(tree, [], []);
return {
  menus,
  elements,
  nodes: empty.nodes.length,
  unmatchedMenus: empty.unmatchedMenus,
  unmatchedElements: empty.unmatchedElements,
};
"""
    )

    assert result["menus"] == ["menu:a"]
    assert result["elements"] == ["element:a1"]
    assert result["nodes"] == 0
    assert result["unmatchedMenus"] == []
    assert result["unmatchedElements"] == []
