"""用户「数据资产」授权里的直配 / 角色继承口径（智能体、知识库、数据集、外部 API、可用模型）。

背景：编辑用户 → 权限配置 → 数据资产下的五个页签此前只回显 `direct_permissions`。
`GET /users/{id}/permissions` 本来就同时返回并集（`permissions` = 用户 ∪ 业务角色 ∪
全员基线）与直配（`direct_permissions`），但前端只消费了后者，于是角色已授予的资产在
页面上**完全不出现**——管理员会以为「没配过」再手动勾一遍。

真正的风险在后端：`update_user_permissions` 只对 `forbidden_tool / forbidden_command /
forbidden_skill` 剔除角色派生项，**agents / datasets / metadata / apis / models 这五类
不剔除**。重复勾选会把继承项写成用户直配行，角色侧之后取消授权时该用户仍然保留，
页面上也再也解释不清是谁给的。

因此这里锁三件事：
1. 五类的「并集 − 直配」都要算得出来（不是只算禁用项和菜单）；
2. 旧后端没有 `direct_permissions` 时不得凭空造出「角色已有」（标错来源比不标更难排查）；
3. 继承项永远没有进入 payload 的通道，页签计数把直配与继承分开算。
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


def _permissions(**overrides):
    lists = {
        "agents": [],
        "datasets": [],
        "metadata": [],
        "apis": [],
        "menus": [],
        "elements": [],
        "forbidden_tools": [],
        "forbidden_commands": [],
        "forbidden_skills": [],
        "models": [],
    }
    lists.update(overrides)
    return lists


# 场景：林清和被授予「数据分析师」角色，角色给了智能体 / 知识库 / 数据集 / API / 模型，
# 其中数据集还额外有一条全员基线授权；用户自己又直配了各一类。
EFFECTIVE = _permissions(
    agents=["agent-direct", "agent-role", "agent-role-2"],
    datasets=["kb-direct", "kb-role", "kb-all-members"],
    metadata=["meta-role", "meta-all-members"],
    apis=["api-direct", "api-role"],
    models=["model-role"],
)
DIRECT = _permissions(
    agents=["agent-direct"],
    datasets=["kb-direct"],
    apis=["api-direct"],
)


def test_inherited_assets_cover_all_five_tabs():
    """五个页签都要算出「角色/全员已有」：一个都不能漏，顺序按并集保序。"""
    result = _run_typescript(
        f"""
const state = api.resolveUserPermissionEditState({{
  permissions: {json.dumps(EFFECTIVE)},
  direct_permissions: {json.dumps(DIRECT)},
}});
return state.inheritedAssets;
"""
    )

    assert result["agents"] == ["agent-role", "agent-role-2"]
    assert result["datasets"] == ["kb-role", "kb-all-members"], "全员基线授权也必须算进来"
    assert result["metadata"] == ["meta-role", "meta-all-members"]
    assert result["apis"] == ["api-role"]
    assert result["models"] == ["model-role"]
    assert set(result.keys()) == {"agents", "datasets", "metadata", "apis", "models"}


def test_direct_items_never_leak_into_inherited_assets():
    """直配项自己不能被标成「角色已有」，否则管理员会放弃调整一个他其实能改的授权。"""
    result = _run_typescript(
        f"""
const state = api.resolveUserPermissionEditState({{
  permissions: {json.dumps(EFFECTIVE)},
  direct_permissions: {json.dumps(DIRECT)},
}});
return {{
  inherited: state.inheritedAssets,
  directAgents: state.direct.agents,
  directDatasets: state.direct.datasets,
}};
"""
    )

    assert result["directAgents"] == ["agent-direct"]
    assert result["directDatasets"] == ["kb-direct"]
    for tab, ids in result["inherited"].items():
        assert "agent-direct" not in ids or tab != "agents"
        assert "kb-direct" not in ids or tab != "datasets"


def test_missing_direct_permissions_never_invents_inherited_assets():
    """旧后端没有 direct_permissions：无法区分继承，五类一律为空，且标记 fallback。"""
    result = _run_typescript(
        f"""
const state = api.resolveUserPermissionEditState({{ permissions: {json.dumps(EFFECTIVE)} }});
return {{ inheritedAssets: state.inheritedAssets, fallback: state.usingEffectiveFallback }};
"""
    )

    assert result["inheritedAssets"] == {
        "agents": [],
        "datasets": [],
        "metadata": [],
        "apis": [],
        "models": [],
    }, "标错来源（把用户直配说成角色给的）比不标更难排查"
    assert result["fallback"] is True


def test_malformed_direct_permissions_also_fall_back():
    """`direct_permissions` 是 null / 数组 / 字符串时同样回退，不得当成「没有直配项」。"""
    result = _run_typescript(
        f"""
return [null, [], 'nope'].map((value) => {{
  const state = api.resolveUserPermissionEditState({{
    permissions: {json.dumps(EFFECTIVE)},
    direct_permissions: value,
  }});
  return {{
    inheritedAgents: state.inheritedAssets.agents,
    fallback: state.usingEffectiveFallback,
  }};
}});
"""
    )

    for entry in result:
        assert entry["inheritedAgents"] == []
        assert entry["fallback"] is True


def test_is_inherited_asset_id_tolerates_dirty_values():
    """脏数据（null / 空白 / 数字 id）不得让判定抛错，也不得误判。"""
    result = _run_typescript(
        """
const inherited = ['sys-agent-chat', ' 74c4b19f ', null];
return {
  exact: api.isInheritedAssetId('sys-agent-chat', inherited),
  padded: api.isInheritedAssetId('74c4b19f', inherited),
  absent: api.isInheritedAssetId('sys-agent-kb', inherited),
  emptyId: api.isInheritedAssetId('', inherited),
  nullId: api.isInheritedAssetId(null, inherited),
  emptyList: api.isInheritedAssetId('sys-agent-chat', []),
  nullList: api.isInheritedAssetId('sys-agent-chat', null),
};
"""
    )

    assert result == {
        "exact": True,
        "padded": True,
        "absent": False,
        "emptyId": False,
        "nullId": False,
        "emptyList": False,
        "nullList": False,
    }


def test_asset_grant_counts_add_direct_and_inherited():
    """页签徽标口径：直配 + 继承 = 该用户实际可用条数；禁用资源页签不走这个函数。"""
    result = _run_typescript(
        """
return {
  both: api.countAssetGrants(['a', 'b'], ['c']),
  onlyDirect: api.countAssetGrants(['a'], []),
  onlyInherited: api.countAssetGrants([], ['x', 'y']),
  empty: api.countAssetGrants(undefined, null),
};
"""
    )

    assert result["both"] == {"direct": 2, "inherited": 1, "total": 3}
    assert result["onlyDirect"] == {"direct": 1, "inherited": 0, "total": 1}
    assert result["onlyInherited"] == {"direct": 0, "inherited": 2, "total": 2}
    assert result["empty"] == {"direct": 0, "inherited": 0, "total": 0}


def test_payload_built_from_direct_only_never_contains_inherited_assets():
    """提交口径：payload 只由直配构建，继承项 id 一个都不能出现。"""
    result = _run_typescript(
        f"""
const state = api.resolveUserPermissionEditState({{
  permissions: {json.dumps(EFFECTIVE)},
  direct_permissions: {json.dumps(DIRECT)},
}});
return api.buildUserPermissionPayload(state.direct);
"""
    )

    assert result["agents"] == ["agent-direct"]
    assert result["datasets"] == ["kb-direct"]
    assert result["apis"] == ["api-direct"]
    assert result["metadata"] == []
    assert result["models"] == []
    for tab in ("agents", "datasets", "metadata", "apis", "models"):
        for inherited_id in (
            "agent-role",
            "agent-role-2",
            "kb-role",
            "kb-all-members",
            "meta-role",
            "meta-all-members",
            "api-role",
            "model-role",
        ):
            assert inherited_id not in result[tab], f"{inherited_id} 不该出现在 {tab} 的 payload 里"


def test_users_page_wires_inherited_assets_into_assets_tabs():
    """源码契约：用户页必须真的把继承资产接到卡片、全选与页签计数上。

    纯函数算得再对，只要 templates 里还用旧的 `v-model` 直接改数组、全选不看继承，
    这些问题就会原样复现在线上：
      - 卡片：勾选态与禁用态都要走 `isAssetChecked` / `isAssetDisabled`；
      - 全选：`selectableResources` 必须排除继承项，否则全选会把继承写成直配；
      - 页签：计数要把直配与继承分开（`resourceTabInheritedCount`）。
    """
    users = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")

    assert "inheritedAssets" in users, "用户页必须保存「并集 − 直配」的资产集合"
    assert "resolveUserPermissionEditState(" in users
    assert "isAssetChecked(res)" in users, "卡片勾选态要走统一判定（直配 + 继承）"
    assert "isAssetDisabled(res)" in users, "继承项必须禁用勾选"
    assert "角色/全员已有" in users, "卡片要标出只读来源"
    assert "resourceTabInheritedCount(" in users, "页签徽标要能区分直配与继承条数"
    assert "assetScopeFilter" in users, "要有「仅角色已有」筛选入口"
    assert "仅角色已有" in users

    # 全选：selectableResources 里必须出现继承排除；否则一次全选就固化成用户直配
    selectable = users.split("const selectableResources = computed(")[1].split("const isAllSelected")[0]
    assert "isInheritedAsset" in selectable, "全选候选必须排除角色/全员已有的项"

    # 卡片不再直接把数组塞进 v-model：继承项没有写入通道
    card_block = users.split("v-for=\"res in modelResourceEntries\"")[1].split("</label>")[0]
    assert "v-model=\"(permissionData as any)[activeResTab]\"" not in card_block
    assert "@change=\"toggleAssetResource(res)\"" in card_block


def test_select_all_only_touches_candidates_and_never_clears_on_empty():
    """「本页全选 / 清空」的两条边界，都是「不在用户看不见的地方改数据」。

    回归背景：候选为空（筛选停在「仅角色已有」）时旧实现走 else 分支把整份直配赋成空数组，
    一次点击静默删掉全部直配；「清空」也是整份覆盖，把已失联但保留授权的库一起抹掉。
    """
    result = _run_typescript(
        """
return {
  // 筛选=仅角色已有 → 候选空：直配必须原样不动
  emptyCandidates: api.applyAssetSelectAll(['kb-a', 'kb-b'], [], true),
  emptyCandidatesSelect: api.applyAssetSelectAll(['kb-a'], null, false),
  // 清空只去候选，候选外的（失联但保留授权的库）留下
  clearKeepsOutside: api.applyAssetSelectAll(['kb-a', 'kb-missing'], ['kb-a'], true),
  // 全选追加候选，且保留候选外的既有直配
  selectKeepsOutside: api.applyAssetSelectAll(['kb-missing'], ['kb-a', 'kb-b'], false),
  // 已有项不重复追加
  selectNoDup: api.applyAssetSelectAll(['kb-a'], ['kb-a', 'kb-b'], false),
  // 脏数据按空数组
  dirty: api.applyAssetSelectAll(null, undefined, false),
};
"""
    )

    assert result["emptyCandidates"] == ["kb-a", "kb-b"], "候选为空时绝不清空直配"
    assert result["emptyCandidatesSelect"] == ["kb-a"]
    assert result["clearKeepsOutside"] == ["kb-missing"]
    assert result["selectKeepsOutside"] == ["kb-missing", "kb-a", "kb-b"]
    assert result["selectNoDup"] == ["kb-a", "kb-b"]
    assert result["dirty"] == []


def test_asset_scope_filter_is_reset_with_permission_buffers():
    """筛选必须随权限缓冲一起复位，且声明在复位函数之前。

    两个都会让页面「空掉且无法恢复」：
      - 残留「仅角色已有」到下一个用户：新用户没有继承项时汇总条/筛选按钮整块不渲染，
        列表却被过滤成空，管理员找不到切回「全部」的入口；
      - 声明晚于 `resetPermissionBuffers` 定义会被 TDZ 撞上（模拟量在 setup 期调用即报错）。
    """
    users = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")

    reset_idx = users.index("const resetPermissionBuffers = () => {")
    reset_block = users[reset_idx : users.index("\n};", reset_idx)]
    assert 'assetScopeFilter.value = "all"' in reset_block, "关闭弹窗/切换用户必须复位来源筛选"

    decl_idx = users.index("const assetScopeFilter = ref<AssetScopeFilter>")
    assert decl_idx < reset_idx, "筛选声明必须早于 resetPermissionBuffers，否则运行期会踩 TDZ"

    # 数据整体重拉（含 409 冲突重拉）也要复位，否则列表会因旧筛选变空
    fetch_idx = users.index("const fetchUserPermissions = async")
    fetch_block = users[fetch_idx : fetch_idx + 6000]
    assert 'assetScopeFilter.value = "all"' in fetch_block

    # 全选候选必须尊重筛选，否则会在隐藏项上动手
    selectable = users[users.index("const selectableResources = computed(") : users.index("const isAllSelected")]
    assert "matchesAssetScope(r)" in selectable


def test_select_all_wiring_goes_through_the_pure_function():
    """全选接线必须走纯函数，且空候选时早退——防的是「整份赋空数组」的旧写法复活。"""
    users = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")
    start = users.index("const toggleSelectAll = () => {")
    block = users[start : users.index("\n};", start)]

    assert "applyAssetSelectAll(" in block
    assert "permissionData.value[type] = [];" not in block, "不得整份清空直配缓冲"
    assert "selectableResources.value.map" in block, "候选必须来自可见可直配集合"


def test_private_datasets_are_stripped_from_inherited_assets_too():
    """私有库授权在判定里会被跳过：角色集合同样要剔除，否则看着像对方真有权限。

    `fetchResources` 与 `fetchUserPermissions` 是并行的，拉权限那一刻知识库列表可能还没
    回来，`collectPrivateDatasetIds()` 是空集——所以资源侧必须再过滤一次（直配与继承都要）。
    """
    users = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")
    idx = users.index("const privateIds = collectPrivateDatasetIds();")
    block = users[idx : idx + 900]

    assert "permissionData.value.datasets" in block
    assert "inheritedAssets.value.datasets" in block, "继承集合也要剔除私有库"
    assert "!privateIds.has(id)" in block


def test_empty_state_explains_when_a_filter_hid_everything():
    """列表被筛选清空时不能只说「暂无可用资源」——那会被读成「这个用户没有资源可分配」。"""
    users = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")
    start = users.index("const emptyResourceText = computed(")
    block = users[start : users.index("\n});", start)]

    assert 'assetScopeFilter.value !== "all"' in block
    assert "当前筛选下没有角色/全员已有的资源" in block
    assert "当前筛选下没有用户直配的资源" in block
