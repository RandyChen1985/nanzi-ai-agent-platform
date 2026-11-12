"""元数据管理「业务分组」页签契约：分组口径、页签计数、表单字段、后端字段与迁移。

需求来源：用户「我需要元数据管理里面也支持这样的业务分组，包括搜索，你先 mockhtml 给我看看」，
确认原型后「新增字段，按 a」。方案见 `prototypes/2026-11-12-metadata-dataset-group-tabs.html`
（方案 A：新增单值 `meta_datasets.group_name`，页签与筛选共用 `groupFilter`）。

与 `tests/frontend/test_tool_group_tabs_contract.py` 的分工：那边锁工具页（API / 脚本），
这边锁**数据集**形状——重点是两处容易错位的地方：
1. 数据集是 `status` 整型，页面得先映射成 `is_active` 再交给共用纯函数；
2. 数据集的搜索命中 name / display_name / description，工具的默认字段里没有 display_name。

页签计算**真跑** `frontend/src/utils/toolGroupTabs.ts`（Node 探针加载真实模块），所以测的就是
页面渲染页签时用的那份计算，不是测试里另写一遍的近似实现。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/MetadataDatasets.vue"
PROBE = ROOT / "tests/frontend/metadata_dataset_group_probe.cjs"
MODEL = ROOT / "app/models/metadata.py"
SCHEMAS = ROOT / "app/schemas/metadata.py"
CLONE_SERVICE = ROOT / "app/services/dataset_clone_service.py"
METADATA_SERVICE = ROOT / "app/services/metadata_service.py"
MIGRATION_MYSQL = ROOT / "db-prod/V213-add-dataset-group-name.sql"
MIGRATION_PG = ROOT / "db-prod-pg/V113-add-dataset-group-name.sql"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def view() -> str:
    return _source(VIEW)


@pytest.fixture(scope="module")
def probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：页签数据（12 个数据集，3 个业务分组 3/3/2 + 4 个未分组）
# --------------------------------------------------------------------------


def test_tabs_order_and_counts(probe: dict) -> None:
    """「全部」打头、各组按命中数降序、「未分组」固定末位。"""
    assert probe["base_total"] == 12
    assert probe["base_ungrouped"] == 4
    assert probe["base_first_key"] == "all"
    assert probe["base_last_key"] == "__ungrouped__"
    assert probe["base_items"] == [
        ["全部", 12],
        ["财务核算", 3],
        ["销售经营", 3],
        ["供应链", 2],
        ["未分组", 4],
    ]


def test_status_filter_feeds_tab_counts(probe: dict) -> None:
    """状态筛选参与页签计数：只看已启用时，每个组的数字都要跟着变小。

    这是「页签写着 N 个、切过去只有 M 个」类故障的防线——页面必须把 status 整型
    映射成 is_active 再交给同一个纯函数，而不是页签自己数一遍全量。
    """
    assert probe["active_total"] == 9
    assert dict(probe["active_items"])["财务核算"] == 2
    assert dict(probe["active_items"])["未分组"] == 3
    assert probe["active_total"] < probe["base_total"]


def test_keyword_searches_display_name(probe: dict) -> None:
    """数据集搜索要命中显示名；同时证明 searchFields 真的传进去了。

    「交易事实」只出现在 `sales_orders` 的 display_name（描述里没有、name 里没有）：
    - 带 display_name 的字段配置 → 命中 1 条，且页签只剩「销售经营」；
    - 用工具的默认字段（不含 display_name）→ 0 条。
    """
    assert probe["kw_display_name_total"] == 1
    assert probe["kw_display_name_labels"] == ["全部", "销售经营"]
    assert probe["kw_default_fields_total"] == 0


def test_keyword_searches_description(probe: dict) -> None:
    assert probe["kw_description_total"] == 1


def test_narrowing_keyword_drops_empty_groups(probe: dict) -> None:
    """关键词把某组搜空后，那一组的页签必须消失——点进去空列表比不显示更糟。"""
    assert probe["kw_narrow_total"] == 1
    assert probe["kw_narrow_items"] == [["全部", 1], ["供应链", 1]]


def test_no_ungrouped_tab_when_everything_grouped(probe: dict) -> None:
    assert probe["all_grouped_has_ungrouped"] is False


def test_blank_group_names_collapse_into_ungrouped(probe: dict) -> None:
    """空串 / 纯空格 / null 都是「未分组」，不能各自变成一个页签；分组名前后空格要裁掉。"""
    assert probe["blank_group_is_empty"] is True
    assert probe["null_group_is_empty"] is True
    assert probe["trimmed_group"] == "财务核算"


def test_filter_and_tab_count_share_one_rule(probe: dict) -> None:
    """页签「全部」上的数字，必须等于逐条用 matchesToolFilters 判定命中的条数。

    两者只要漂移，就会出现「页签说 3 个、列表里 1 个」这种分不清是谁错的故障。
    """
    inactive = dict(probe["inactive_items"])
    assert probe["consistency"] == inactive["全部"] == 3
    # 各组 + 未分组 加起来也要等于「全部」，否则有条目既不在任何组也没算进未分组
    assert sum(n for label, n in probe["inactive_items"] if label != "全部") == inactive["全部"]


# --------------------------------------------------------------------------
# 前端源码契约：页签、共用状态、表单、兜底
# --------------------------------------------------------------------------


def test_view_mounts_shared_tab_component(view: str) -> None:
    """复用工具注册表的页签组件与纯函数，两页行为才一致（也才有同一个溢出方案）。"""
    assert "import ToolGroupTabs from '../components/system/ToolGroupTabs.vue'" in view
    assert "computeGroupTabs" in view
    assert '<ToolGroupTabs v-model="groupFilter" :items="datasetGroupTabs"' in view


def test_view_does_not_reimplement_filtering(view: str) -> None:
    """页面只消费纯函数的结果，不自己再写一遍过滤，避免两套口径漂移。"""
    assert "matchesToolFilters" not in view


def test_tabs_and_card_badge_share_group_filter(view: str) -> None:
    """页签、卡片徽标、列表标签写的都是同一个 groupFilter——点徽标等于点页签。"""
    assert "const groupFilter = ref('all')" in view
    assert 'v-model="groupFilter" :items="datasetGroupTabs"' in view
    assert "@click.stop=\"groupFilter = toolGroupName(ds)\"" in view


def test_group_badge_hidden_while_locked(view: str) -> None:
    """锁定某一组时不再逐条显示分组（每条都是这个组，纯重复信息）；列表视图同理。"""
    assert '<div v-if="groupFilter === \'all\'" class="mt-1.5 flex items-center gap-1">' in view
    assert 'v-if="groupFilter === \'all\' && toolGroupName(ds)"' in view


def test_display_list_applies_group_filter(view: str) -> None:
    """分组过滤落在 displayDatasets 里，配合搜索 / 状态一起生效。"""
    assert "if (groupFilter.value !== 'all') {" in view
    assert "groupFilter.value === UNGROUPED_GROUP ? !name : name === groupFilter.value" in view


def test_stale_group_falls_back_to_all(view: str) -> None:
    """当前分组被搜空 / 分组名被改掉时回「全部」，并跳过首屏加载期（否则刚选的分组被清掉）。"""
    assert "watch(datasetGroupTabs," in view
    assert "datasets.value.length > 0 && groupFilter.value !== 'all'" in view


def test_forms_expose_group_name_field(view: str) -> None:
    """新建与编辑都要能填分组：两处 v-model 各自独立，共用同一份候选列表。"""
    assert 'v-model="newDataset.group_name"' in view
    assert 'v-model="editingDataset.group_name"' in view
    assert view.count('list="dataset-group-options"') == 2
    assert '<datalist id="dataset-group-options">' in view


def test_group_name_normalized_before_save(view: str) -> None:
    """空串 / 前后空格在提交前归一为 null，否则「空格分组」会变成一个真实页签。"""
    assert "const normalizeGroupName" in view
    assert "group_name: normalizeGroupName(newDataset.value.group_name)" in view
    assert "editingDataset.value.group_name = normalizeGroupName(editingDataset.value.group_name)" in view


def test_group_hint_separates_group_from_tags(view: str) -> None:
    """文案必须把「分组」与「业务标签」的分工说清：分组不参与权限、不进 AI 提示词。"""
    assert "const businessGroupHint" in view
    assert "不参与数据权限判定" in view
    assert "不会写进 AI 提示词" in view
    # 标签那份说明不能被顺手改掉：它承担「标签会进 AI 数据集目录」的口径
    assert "const businessTagHint" in view


# --------------------------------------------------------------------------
# 弹窗排版契约：字段两列、限高滚动、按钮常驻
#
# 起因：用户反馈「新建/编辑页太高了，我都看不到按钮了」。根因是两层叠加——
# 6 个字段全竖排 + 弹窗「垂直居中 + overflow:hidden」，内容比视口高时上下被同时裁掉
# 且无法滚动，按钮不是「要滚一下」而是彻底点不到。只缩字段不改容器，矮屏幕照样复发。
# --------------------------------------------------------------------------


def test_modals_are_height_capped_and_scrollable(view: str) -> None:
    """两个弹窗都要：容器限高、body 内部滚动、头尾不参与滚动。"""
    assert view.count("max-h-[calc(100vh-2rem)]") == 2
    assert view.count("min-h-0 flex-1 space-y-4 overflow-y-auto") == 2
    assert view.count("flex shrink-0 justify-end gap-3 border-t border-gray-100 bg-gray-50 px-6 py-3") == 2
    # 外层允许滚动：极端情况（超小屏 + 长标签）仍能滚到按钮
    assert view.count("items-start justify-center overflow-y-auto bg-black/50") == 2


def test_modals_put_paired_fields_on_one_row(view: str) -> None:
    """物理名称↔显示名称、业务分组↔业务标签 各占一行；窄屏回单列。

    每个弹窗 2 处两列栅格（身份行 + 分组/标签行），新建与编辑共 4 处。
    """
    assert view.count("grid grid-cols-1 gap-4 sm:grid-cols-2") == 4
    # 不再回到「max-w-lg 且整块 overflow-hidden」的老样子
    assert "shadow-2xl w-full max-w-lg overflow-hidden" not in view


def test_data_source_can_only_be_picked(view: str) -> None:
    """数据源 ID 只能选不能手输（用户反馈：手输一个拼错的名字不会报错，只会在查数时连不上）。

    弹窗里必须是 `<select>`，不再留可自由输入的 input。
    """
    assert '<input v-model="newDataset.data_source"' not in view
    assert '<input v-model="editingDataset.data_source"' not in view
    assert view.count('v-model="newDataset.data_source"') == 1
    assert view.count('v-model="editingDataset.data_source"') == 1

    # 两个 select 都带着「使用系统默认」这一档
    assert view.count('<option value="">使用系统默认（{{ defaultDataSource }}）</option>') == 2


def test_unregistered_data_source_value_is_preserved(view: str) -> None:
    """存量数据集可能有「未在数据源管理登记」的 data_source（配置被删、早期手输遗留）。

    这类值必须原样留在选项里，否则下拉一渲染就成空选，用户一保存就把配置悄悄清了。
    """
    assert "const isUnregisteredDataSource" in view
    assert view.count("isUnregisteredDataSource(newDataset.data_source)") == 1
    assert view.count("isUnregisteredDataSource(editingDataset.data_source)") == 1
    # 打开弹窗时刷新已配置数据源，避免刚在数据源管理里加完、这里还是旧列表
    assert "watch([showCreateModal, showEditDatasetModal]" in view


# --------------------------------------------------------------------------
# 后端契约：模型、schema、审计、复制、迁移
# --------------------------------------------------------------------------


def test_model_has_group_name_column() -> None:
    src = _source(MODEL)
    assert "group_name = Column(String(64), nullable=True, comment='业务分组')" in src
    assert "group_name = Column(String(64), nullable=False" not in src


def test_schemas_accept_group_name() -> None:
    """创建 / 更新 / 复制三个入口都要收这个字段，缺一个就会出现「填了不生效」。

    除了收，还必须与另外三处同口径（`app/utils/group_name.py`）：空白串归 None、
    超长按 64 截断。原先这三处是裸的 `Optional[str] = None`，`"   "` 会落成一个
    看不见的空分组页签。行为断言见
    `tests/api/portal/test_dataset_business_group_contract.py`。
    """
    src = _source(SCHEMAS)
    declared = "group_name: Optional[str] = Field(default=None, max_length=GROUP_NAME_MAX_LEN)"
    dataset_base = src.split("class DatasetBase(BaseModel):")[1].split("class DatasetCreate")[0]
    assert declared in dataset_base
    dataset_update = src.split("class DatasetUpdate(BaseModel):")[1].split("class DatasetResponse")[0]
    assert declared in dataset_update
    clone_req = src.split("class CloneDatasetRequest(BaseModel):")[1].split("class BatchDeleteDatasetsRequest")[0]
    assert declared in clone_req
    # 三处都要挂同一个归一 validator
    assert src.count('@field_validator("group_name", mode="before")') >= 3
    assert "from app.utils.group_name import GROUP_NAME_MAX_LEN, normalize_group_name" in src


def test_changelog_records_group_name() -> None:
    """变更日志要带上分组，否则审计里看不出「谁把这个数据集挪到别的业务域了」。"""
    src = _source(METADATA_SERVICE)
    assert '"group_name": dataset.group_name,' in src
    assert '"group_name": old_dataset.group_name,' in src
    assert '"group_name": new_dataset.group_name,' in src


def test_clone_inherits_source_group() -> None:
    """复制数据集默认沿用源分组：复制出来的库通常仍属同一个业务域。

    但「默认沿用」不能吃掉显式清空：调用方传 `group_name: null` 的意思就是「副本不分组」，
    用 `is not None` 判断永远分不清「没提这个字段」与「明确要清空」。所以判据必须是
    `model_fields_set`。
    """
    src = _source(CLONE_SERVICE)
    assert '"group_name" in payload.model_fields_set' in src
    assert "if payload.group_name is not None else source.group_name" not in src


def test_migrations_exist_for_both_databases() -> None:
    """两套迁移不可混用：MySQL 与 PG 各一份，都是幂等写法。"""
    mysql = _source(MIGRATION_MYSQL)
    assert "ALTER TABLE `meta_datasets`" in mysql
    assert "`group_name` VARCHAR(64) NULL" in mysql

    pg = _source(MIGRATION_PG)
    assert 'ALTER TABLE "meta_datasets"' in pg
    assert 'ADD COLUMN IF NOT EXISTS "group_name" VARCHAR(64) NULL' in pg
