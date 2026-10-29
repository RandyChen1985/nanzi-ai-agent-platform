"""勾功能点必须补齐祖先菜单：只授功能点不授父菜单是「死权限」。

背景：用户在「角色管理 → 界面权限」里勾了「元数据管理」下面的功能点，保存后左侧
菜单仍然没有「元数据管理」，「我的权限」里它还被标成「仅分组」。根因不是保存失败，
而是权限表里只有 `element:metadata:*`、没有 `menu:metadata`：

- 侧栏只查 `menus`（`hasPermission('menu:metadata')`）→ 入口不显示；
- 页面进不去，那些功能点永远点不到；
- 「我的权限」把自身未授权、仅因后代出现的节点标成「仅分组」，看起来像没生效。

这里锁三件事：

1. 前端纯函数：勾功能点时补齐整条祖先 `menu:*` 链（跳过 `group:*`）；
2. 前端纯函数：角色已授予的菜单不写成直配（否则角色侧取消后用户仍保留，且会
   被误认为管理员自己配的）；
3. 服务端兜底：任何入口只提交 element 时，`_with_ancestor_menus` 补上父菜单。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/permissionLists.ts"
pytestmark = pytest.mark.no_infrastructure

TREE_JSON = json.dumps(
    [
        {
            "id": "group:chatbi",
            "label": "ChatBI 开发平台",
            "children": [
                {
                    "id": "menu:metadata",
                    "label": "元数据管理",
                    "children": [
                        {"id": "element:metadata:sync", "label": "同步到 RAGFlow", "children": []},
                        {"id": "element:metadata:view_yaml", "label": "查看 YAML", "children": []},
                    ],
                },
                {"id": "menu:data_sources", "label": "数据源管理", "children": []},
            ],
        }
    ]
)


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
const tree = {TREE_JSON};
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


def test_ancestor_menu_ids_skips_groups():
    result = _run("return api.getAncestorMenuIds(tree, 'element:metadata:sync')")
    # group:chatbi 没有授权语义，不往上传
    assert result == ["menu:metadata"]


def test_ancestor_menu_ids_for_nested_menu():
    result = _run("return api.getAncestorMenuIds(tree, 'menu:metadata')")
    assert result == []


def test_toggling_element_grants_its_page_menu():
    """用户截图那条：勾功能点后「元数据管理」菜单必须跟着被授予。"""
    result = _run("""
return api.applyElementToggle([], [], 'element:metadata:sync', tree)
""")
    assert result["elements"] == ["element:metadata:sync"]
    assert result["menus"] == ["menu:metadata"]


def test_toggling_element_off_keeps_parent_menu():
    """取消功能点不回退父菜单：同页可能还有别的功能点要留，静默回收会造成意外。"""
    result = _run("""
return api.applyElementToggle(
  ['menu:metadata', 'menu:data_sources'],
  ['element:metadata:sync', 'element:metadata:view_yaml'],
  'element:metadata:sync',
  tree,
)
""")
    assert result["elements"] == ["element:metadata:view_yaml"]
    assert result["menus"] == ["menu:metadata", "menu:data_sources"]


def test_inherited_menu_is_not_written_as_direct_grant():
    """角色已给的菜单不写进直配，否则角色侧取消后该用户仍保留。"""
    result = _run("""
return api.applyElementToggle([], [], 'element:metadata:sync', tree, ['menu:metadata'])
""")
    assert result["elements"] == ["element:metadata:sync"]
    assert result["menus"] == []


def test_unknown_element_id_does_not_invent_parent():
    result = _run("""
return api.applyElementToggle([], [], 'element:custom:whatever', tree)
""")
    assert result["elements"] == ["element:custom:whatever"]
    assert result["menus"] == []


# ---- 服务端兜底 ----


def test_service_backfills_ancestor_menus_from_elements():
    from app.services.permission_service import _with_ancestor_menus

    assert _with_ancestor_menus([], ["element:metadata:view_yaml"]) == ["menu:metadata"]
    # 已有父菜单时不重复追加
    assert _with_ancestor_menus(["menu:metadata"], ["element:metadata:view_yaml"]) == ["menu:metadata"]
    # 未知 id 不臆造父级
    assert _with_ancestor_menus([], ["element:no_such_element"]) == []
    # 只有菜单时原样返回
    assert _with_ancestor_menus(["menu:a"], []) == ["menu:a"]
