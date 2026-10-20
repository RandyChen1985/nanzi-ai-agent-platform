"""用户权限「界面访问」的直配 / 角色继承分离，以及菜单勾选的纯函数行为测试。

背景：`GET /users/{id}/permissions` 返回并集（`permissions`）与直配（`direct_permissions`），
而前端只对 `forbidden_*` 算过差集；菜单（menus）与功能点（elements）的「并集 − 直配」
从未计算，导致编辑用户时看不到「哪些页面已由角色授予」。

用户在 2026-10-20 反馈了两个交互缺陷：

1. 无法只勾菜单、不勾功能点——旧实现「勾菜单 = 级联全选子项」，这个组合根本无法表达；
2. 勾菜单自动勾上的功能点取消后，父级因自身仍在直配里被判定为「全勾」，再点父级反而
   清空全部，看起来像「取消不掉」。

因此勾选语义拆成两个显式动作（`applyMenuEntryToggle` / `applyMenuCascadeToggle`），
并把「自身已授予」与「整棵子树已授予」两个判定分开。这里全部打在纯函数上，不依赖 Vue。

级联的 `select` 仍必须跳过「已由角色授予」的子项：后端对 menus / elements 不做角色派生
剔除（只对 forbidden_* 做），连继承项一起写会造出冗余直配行——角色侧日后取消，用户这里
仍然保留。
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


# ---------------------------------------------------------------- 菜单入口（解耦）


def test_selecting_menu_grants_only_the_entry():
    """勾菜单只授予入口：不得把任何功能点写进直配（用户诉求 1）。"""
    result = _run_typescript(
        """
return api.applyMenuEntryToggle(
  [],
  [],
  'menu:data_sources',
  ['element:data_sources:edit', 'element:data_sources:delete'],
);
"""
    )

    assert result["menus"] == ["menu:data_sources"]
    assert result["elements"] == [], "勾菜单不再是「全选」——功能点必须单独勾选"


def test_deselecting_menu_clears_its_direct_children_only():
    """取消入口：清掉自己的直配子项，其他菜单的子项不受影响。"""
    result = _run_typescript(
        """
return api.applyMenuEntryToggle(
  ['menu:meta', 'menu:data'],
  ['element:sync', 'element:edit', 'element:unrelated'],
  'menu:meta',
  ['element:sync', 'element:edit'],
);
"""
    )

    assert result["menus"] == ["menu:data"]
    assert result["elements"] == ["element:unrelated"], "其他菜单/独立功能点不得被误删"


def test_cancelling_a_child_does_not_silently_regrant_it():
    """取消单个功能点后再点父级复选框：父级取消入口（清空剩余直配），不会「自动勾回」。

    旧实现里父级仍被判定为「全勾」，点一下直接清空全部；现在父级只切自身，
    语义可预测，恢复全选走「全选」按钮。
    """
    result = _run_typescript(
        """
const full = api.applyMenuCascadeToggle([], [], 'menu:meta', ['element:a', 'element:b'], 'select');
const afterChildCancel = {
  menus: full.menus,
  elements: full.elements.filter((id) => id !== 'element:b'),
};
const entryOff = api.applyMenuEntryToggle(
  afterChildCancel.menus,
  afterChildCancel.elements,
  'menu:meta',
  ['element:a', 'element:b'],
);
const refill = api.applyMenuCascadeToggle(
  afterChildCancel.menus,
  afterChildCancel.elements,
  'menu:meta',
  ['element:a', 'element:b'],
  'select',
);
return { full, afterChildCancel, entryOff, refill };
"""
    )

    assert result["full"]["elements"] == ["element:a", "element:b"]
    assert result["afterChildCancel"]["menus"] == ["menu:meta"]
    assert result["afterChildCancel"]["elements"] == ["element:a"], "只取消了 element:b"
    assert result["entryOff"]["menus"] == [], "点父级复选框 = 取消入口"
    assert result["entryOff"]["elements"] == [], "取消入口顺带清空直配子项，不留孤儿"
    assert result["refill"]["menus"] == ["menu:meta"], "「全选」重新授予入口"
    assert result["refill"]["elements"] == ["element:a", "element:b"], "「全选」补回全部功能点"


def test_entry_toggle_on_inherited_menu_is_a_noop():
    """入口由角色授予：用户直配侧不能取消，也不得再写一条冗余直配。"""
    result = _run_typescript(
        """
return api.applyMenuEntryToggle(
  [],
  [],
  'menu:roles',
  ['element:role:edit', 'element:role:delete'],
  ['menu:roles'],
  ['element:role:edit'],
);
"""
    )

    assert result["menus"] == [], "继承入口不得被写成用户直配"
    assert result["elements"] == []


def test_entry_toggle_does_not_mutate_inputs():
    """纯函数：入口切换不得就地修改入参。"""
    result = _run_typescript(
        """
const menus = ['menu:a'];
const elements = ['element:x'];
const inheritedMenus = ['menu:b'];
const out = api.applyMenuEntryToggle(menus, elements, 'menu:c', ['element:x', 'element:y'], inheritedMenus, []);
return { out, menus, elements, inheritedMenus };
"""
    )

    assert result["menus"] == ["menu:a"]
    assert result["elements"] == ["element:x"]
    assert result["out"]["menus"] == ["menu:a", "menu:c"]
    assert result["out"]["elements"] == ["element:x"], "入口切换不碰功能点"


# ---------------------------------------------------------------- 显式级联（全选 / 清空）


def test_cascade_select_skips_inherited_children():
    """「全选」：只把非继承子项写入直配，继承项跳过（否则角色取消后仍残留）。"""
    result = _run_typescript(
        """
return api.applyMenuCascadeToggle(
  ['menu:data'],
  [],
  'menu:meta',
  ['element:sync', 'element:edit', 'element:delete'],
  'select',
  [],
  ['element:edit'],
);
"""
    )

    assert result["menus"] == ["menu:data", "menu:meta"]
    assert result["elements"] == ["element:sync", "element:delete"], (
        "element:edit 已由角色授予，不得写成用户直配"
    )


def test_cascade_select_keeps_existing_direct_children_without_duplicates():
    """已有直配子项不重复追加，顺序保持稳定。"""
    result = _run_typescript(
        """
return api.applyMenuCascadeToggle(
  [],
  ['element:delete', 'element:unrelated'],
  'menu:meta',
  ['element:sync', 'element:edit', 'element:delete'],
  'select',
);
"""
    )

    assert result["menus"] == ["menu:meta"]
    assert result["elements"] == ["element:delete", "element:unrelated", "element:sync", "element:edit"]


def test_cascade_clear_keeps_the_entry_and_other_menus():
    """「清空」只清后代直配，入口本身与无关菜单都保留——取消入口是复选框的职责。"""
    result = _run_typescript(
        """
return api.applyMenuCascadeToggle(
  ['menu:meta', 'menu:data'],
  ['element:sync', 'element:edit', 'element:unrelated'],
  'menu:meta',
  ['element:sync', 'element:edit'],
  'clear',
);
"""
    )

    assert result["menus"] == ["menu:meta", "menu:data"], "入口保留，其他菜单不受影响"
    assert result["elements"] == ["element:unrelated"]


def test_cascade_select_routes_child_menus_into_menus_not_elements():
    """MENU_TREE 的子项混有 menu:* 子菜单（用户管理/角色管理）：必须写进 menus。

    旧实现把 descendantIds 一律推进 elements，于是 `menu:system:users` 被存成
    element 记录——勾了父菜单，子菜单仍显示未勾选，且后端按 menu 类型校验时查不到。
    """
    result = _run_typescript(
        """
return api.applyMenuCascadeToggle(
  [],
  [],
  'menu:system',
  ['menu:system:users', 'element:user:view_key', 'menu:system:roles'],
  'select',
);
"""
    )

    assert result["menus"] == ["menu:system", "menu:system:users", "menu:system:roles"]
    assert result["elements"] == ["element:user:view_key"]


def test_child_menu_granted_by_role_is_skipped_when_cascading_select():
    """子菜单已由角色授予时，「全选」不得把它写成用户直配。"""
    result = _run_typescript(
        """
return api.applyMenuCascadeToggle(
  [],
  [],
  'menu:system',
  ['menu:system:users', 'element:user:view_key'],
  'select',
  ['menu:system:users'],
);
"""
    )

    assert result["menus"] == ["menu:system"]
    assert result["elements"] == ["element:user:view_key"]


def test_cascade_clear_removes_child_menus_too():
    """清空父菜单的子树：子菜单的直配也要一并清掉，否则会留下「父还在、子没了入口」的残留。"""
    result = _run_typescript(
        """
return api.applyMenuCascadeToggle(
  ['menu:system', 'menu:system:users', 'menu:data'],
  ['element:user:view_key'],
  'menu:system',
  ['menu:system:users', 'element:user:view_key'],
  'clear',
);
"""
    )

    assert result["menus"] == ["menu:system", "menu:data"], "只清后代，父入口保留"
    assert result["elements"] == []


def test_cascade_does_not_mutate_inputs():
    """纯函数：级联不得就地修改入参（否则 Vue 的响应式依赖会漏掉更新）。"""
    result = _run_typescript(
        """
const menus = ['menu:a'];
const elements = ['element:x'];
const inherited = ['element:y'];
const out = api.applyMenuCascadeToggle(
  menus,
  elements,
  'menu:b',
  ['element:x', 'element:y', 'element:z'],
  'select',
  [],
  inherited,
);
return { out, menus, elements, inherited };
"""
    )

    assert result["menus"] == ["menu:a"], "入参 menus 被修改了"
    assert result["elements"] == ["element:x"], "入参 elements 被修改了"
    assert result["inherited"] == ["element:y"], "入参 inheritedElements 被修改了"
    assert result["out"]["menus"] == ["menu:a", "menu:b"]
    assert result["out"]["elements"] == ["element:x", "element:z"], (
        "已直配的不重复、已继承的跳过、未授予的补上"
    )


# ---------------------------------------------------------------- 三态与只读边界


def _node_expression(sets: str) -> str:
    return f"""
const node = {{ id: 'menu:meta', label: '元数据管理', children: [
  {{ id: 'element:sync', label: '同步' }},
  {{ id: 'element:edit', label: '编辑' }},
  {{ id: 'menu:meta:sub', label: '子页', children: [
    {{ id: 'element:deep', label: '深层' }},
  ]}},
] }};
const sets = {sets};
return {{
  selfGranted: api.isPermissionNodeSelfGranted(node, sets),
  fullyGranted: api.isPermissionNodeGranted(node, sets),
  partial: api.isPermissionNodePartiallySelected(node, sets),
  selfInherited: api.isPermissionNodeSelfInherited(node, sets),
  grantedLeaves: api.countGrantedLeafElements(node, sets),
  totalLeaves: api.countLeafElements(node),
}};
"""


def test_entry_selected_and_partial_children_are_not_fully_granted():
    """只勾入口 + 一个功能点：自身已授予、整棵子树未全选、半选为 true。

    组件据此显示「已勾选 + 功能点 1/3」，而不是旧实现的「全勾」假象——这正是用户
    反馈「取消一个功能点后看不出取消成功」的根因。
    """
    result = _run_typescript(
        _node_expression(
            "{ menus: ['menu:meta'], elements: ['element:sync'], inheritedMenus: [], inheritedElements: [] }"
        )
    )

    assert result["selfGranted"] is True, "勾了入口，复选框就该是勾选态"
    assert result["fullyGranted"] is False, "还有一个深层功能点没给，不能算全选"
    assert result["partial"] is True
    assert result["grantedLeaves"] == 1
    assert result["totalLeaves"] == 3, "计数只数叶子（深层功能点也要算）"


def test_inherited_entry_does_not_lock_editable_children():
    """只读边界收在节点自身：入口来自角色，子功能点仍可单独直配（后端权限码独立）。"""
    result = _run_typescript(
        _node_expression(
            "{ menus: [], elements: ['element:sync'], inheritedMenus: ['menu:meta'], inheritedElements: [] }"
        )
    )

    assert result["selfGranted"] is True
    assert result["selfInherited"] is True, "入口只来自角色 → 该行只读"
    assert result["fullyGranted"] is False, "子项没全给"


def test_self_inherited_is_false_when_direct_entry_exists():
    """自身同时有直配时不算「只由角色授予」，不能被标成只读。"""
    result = _run_typescript(
        _node_expression(
            "{ menus: ['menu:meta'], elements: [], inheritedMenus: ['menu:meta'], inheritedElements: [] }"
        )
    )

    assert result["selfInherited"] is False
