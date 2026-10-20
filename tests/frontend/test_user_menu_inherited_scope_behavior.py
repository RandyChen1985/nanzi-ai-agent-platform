"""用户权限「界面访问」的直配 / 角色继承分离，以及菜单级联的纯函数行为测试。

背景：`GET /users/{id}/permissions` 返回并集（`permissions`）与直配（`direct_permissions`），
而前端只对 `forbidden_*` 算过差集；菜单（menus）与功能点（elements）的「并集 − 直配」
从未计算，导致编辑用户时看不到「哪些页面已由角色授予」。

同时，菜单勾选级联必须跳过「已由角色授予」的子项：后端对 menus / elements 不做角色派生
剔除（只对 forbidden_* 做），连继承项一起写会造出冗余直配行——角色侧日后取消，用户这里
仍然保留。

这里打在两个纯函数上（`resolveUserPermissionEditState` 的扩展字段、`applyMenuToggle`），
不依赖 Vue，不靠字符串断言。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/permissionLists.ts"
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


# ---------------------------------------------------------------- 差集口径


def test_menu_and_element_inheritance_is_effective_minus_direct():
    """菜单/功能点的「来自角色」= 并集 − 直配，保序去重，直配里没有的并集项一个不漏。"""
    result = _run_typescript(
        """
const state = api.resolveUserPermissionEditState({
  permissions: {
    menus: ['menu:a', 'menu:b', 'menu:c', 'menu:b'],
    elements: ['element:a1', 'element:b1', 'element:b2'],
  },
  direct_permissions: {
    menus: ['menu:a'],
    elements: ['element:a1'],
  },
});
return {
  inheritedMenus: state.inheritedMenus,
  inheritedElements: state.inheritedElements,
  directMenus: state.direct.menus,
  directElements: state.direct.elements,
  fallback: state.usingEffectiveFallback,
};
"""
    )

    assert result["inheritedMenus"] == ["menu:b", "menu:c"], "重复项只保留一次，顺序沿用并集"
    assert result["inheritedElements"] == ["element:b1", "element:b2"]
    assert result["directMenus"] == ["menu:a"], "直配不得被差集污染"
    assert result["directElements"] == ["element:a1"]
    assert result["fallback"] is False


def test_inherited_menus_empty_when_falling_back_to_effective():
    """旧后端没给 direct_permissions 时无法区分继承：两个新字段必须为空，不得误标只读。"""
    result = _run_typescript(
        """
const state = api.resolveUserPermissionEditState({
  permissions: { menus: ['menu:a', 'menu:b'], elements: ['element:a1'] },
});
return {
  inheritedMenus: state.inheritedMenus,
  inheritedElements: state.inheritedElements,
  directMenus: state.direct.menus,
  fallback: state.usingEffectiveFallback,
};
"""
    )

    assert result["inheritedMenus"] == [], "无法区分继承时不得凭空造出只读项"
    assert result["inheritedElements"] == []
    assert result["directMenus"] == ["menu:a", "menu:b"], "回退时整块按直配处理，保持原观感"
    assert result["fallback"] is True


def test_inherited_menus_tolerate_bad_shapes():
    """坏数据（null / 字符串 / 非字符串元素）按空数组处理，不影响其他字段。"""
    result = _run_typescript(
        """
const state = api.resolveUserPermissionEditState({
  permissions: { menus: 'menu:a', elements: [null, 'element:a1', 42] },
  direct_permissions: { menus: null, elements: ['element:a1'] },
});
return {
  inheritedMenus: state.inheritedMenus,
  inheritedElements: state.inheritedElements,
  directElements: state.direct.elements,
};
"""
    )

    assert result["inheritedMenus"] == []
    assert result["inheritedElements"] == ["42"], "数字元素按字符串保留，null 丢弃"
    assert result["directElements"] == ["element:a1"]


# ---------------------------------------------------------------- 级联口径


def test_selecting_menu_skips_inherited_children():
    """勾菜单：只把「非继承」的子项写入直配，继承项跳过（否则角色取消后仍残留）。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  ['menu:data'],
  [],
  'menu:meta',
  ['element:sync', 'element:edit', 'element:delete'],
  ['element:edit'],
);
"""
    )

    assert result["menus"] == ["menu:data", "menu:meta"]
    assert result["elements"] == ["element:sync", "element:delete"], (
        "element:edit 已由角色授予，不得写成用户直配"
    )


def test_selecting_menu_keeps_existing_direct_children_without_duplicates():
    """已有直配子项不重复追加，顺序保持稳定。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  [],
  ['element:delete', 'element:unrelated'],
  'menu:meta',
  ['element:sync', 'element:edit', 'element:delete'],
  [],
);
"""
    )

    assert result["menus"] == ["menu:meta"]
    assert result["elements"] == ["element:delete", "element:unrelated", "element:sync", "element:edit"]


def test_deselecting_menu_clears_direct_children_only():
    """取消菜单：移除菜单与全部直配子项；继承项本来就不在直配里，因此无需特殊处理。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  ['menu:meta', 'menu:data'],
  ['element:sync', 'element:edit', 'element:delete', 'element:unrelated'],
  'menu:meta',
  ['element:sync', 'element:edit', 'element:delete'],
  ['element:edit'],
);
"""
    )

    assert result["menus"] == ["menu:data"]
    assert result["elements"] == ["element:unrelated"], "只清该菜单的子项，其他菜单的子项不受影响"


def test_apply_menu_toggle_does_not_mutate_inputs():
    """纯函数：入参数组不得被就地修改（否则 Vue 的响应式依赖会漏掉更新）。"""
    result = _run_typescript(
        """
const menus = ['menu:a'];
const elements = ['element:x'];
const inherited = ['element:y'];
const out = api.applyMenuToggle(
  menus,
  elements,
  'menu:b',
  ['element:x', 'element:y', 'element:z'],
  inherited,
);
return {
  out,
  menus,
  elements,
  inherited,
};
"""
    )

    assert result["menus"] == ["menu:a"], "入参 menus 被修改了"
    assert result["elements"] == ["element:x"], "入参 elements 被修改了"
    assert result["inherited"] == ["element:y"], "入参 inheritedElements 被修改了"
    assert result["out"]["menus"] == ["menu:a", "menu:b"]
    assert result["out"]["elements"] == ["element:x", "element:z"], (
        "已直配的不重复、已继承的跳过、未授予的补上"
    )


def test_toggling_a_fully_inherited_menu_cancels_instead_of_selecting_all():
    """全部子项由角色授予（菜单本身未直配）：视觉是已勾选，点击必须表现为取消。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  [],
  [],
  'menu:roles',
  ['element:role:edit', 'element:role:delete'],
  ['element:role:edit', 'element:role:delete'],
  ['menu:roles'],
);
"""
    )

    assert result["menus"] == [], "已授予的菜单不该被再写一条直配"
    assert result["elements"] == []


def test_cancelling_partially_direct_menu_clears_direct_children_only():
    """部分子项直配、其余由角色授予：点击取消清掉直配子项，继承项仍由角色保留。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  [],
  ['element:sync'],
  'menu:meta',
  ['element:sync', 'element:edit'],
  ['element:edit'],
  [],
);
"""
    )

    assert result["menus"] == []
    assert result["elements"] == [], "直配子项被清掉；element:edit 的权限仍来自角色（不在直配里）"


# ---------------------------------------------------------------- 子菜单分流


def test_selecting_menu_routes_child_menus_into_menus_not_elements():
    """MENU_TREE 的子项混有 menu:* 子菜单（用户管理/角色管理）：必须写进 menus。

    旧实现把 descendantIds 一律推进 elements，于是 `menu:system:users` 被存成
    element 记录——勾了父菜单，子菜单仍显示未勾选，且后端按 menu 类型校验时查不到。
    """
    result = _run_typescript(
        """
return api.applyMenuToggle(
  [],
  [],
  'menu:system',
  ['menu:system:users', 'element:user:view_key', 'menu:system:roles'],
  [],
  [],
);
"""
    )

    assert result["menus"] == ["menu:system", "menu:system:users", "menu:system:roles"]
    assert result["elements"] == ["element:user:view_key"]


def test_child_menu_granted_by_role_is_skipped_when_selecting_parent():
    """子菜单已由角色授予时，勾父菜单不得把它写成用户直配。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  [],
  [],
  'menu:system',
  ['menu:system:users', 'element:user:view_key'],
  [],
  ['menu:system:users'],
);
"""
    )

    assert result["menus"] == ["menu:system"]
    assert result["elements"] == ["element:user:view_key"]


def test_deselecting_menu_clears_child_menus_too():
    """取消父菜单：子菜单的直配也要一并清掉，否则会留下「父没了、子还在」。"""
    result = _run_typescript(
        """
return api.applyMenuToggle(
  ['menu:system', 'menu:system:users', 'menu:data'],
  ['element:user:view_key'],
  'menu:system',
  ['menu:system:users', 'element:user:view_key'],
  [],
  [],
);
"""
    )

    assert result["menus"] == ["menu:data"]
    assert result["elements"] == []
