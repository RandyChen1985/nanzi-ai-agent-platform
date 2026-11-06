"""契约测试：行级策略弹窗保存失败的三处修复。

用户现象：「只是打开不配置任何信息，保存失败？？」，后端日志连续一串
``PUT /api/portal/metadata/datasets/54 400``，页面只弹一句「保存权限配置失败」。

真实原因是后端 `_reject_unusable_row_filter_config` 的 fail-closed 保护：
启用了数据权限却一条带 condition 的规则都没有，运行期兜底是 ``1=0``，
所有人都会查不到数据，所以后端明确拒绝。问题在前端：

1. 不做预校验，让用户撞到 400；
2. catch 把后端那段写得很清楚的中文说明吞成「保存失败」；
3. 用户反复点保存，一屏弹出好几个一样的 toast。

同时修了一个隐患：列表接口给的对象不保证带全行级配置，用它初始化弹窗后
「什么都不改直接保存」会把一份空配置写回去。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODAL = ROOT / "frontend/src/components/metadata/RowPolicyConfigModal.vue"
ENDPOINT = ROOT / "app/api/portal/endpoints/metadata.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_client_pre_checks_same_rule_as_backend() -> None:
    """前端预校验必须和后端同口径：至少一条带 condition 的规则。"""
    modal = _read(MODAL)
    assert "const hasAnyRowRule" in modal
    start = modal.index("const hasAnyRowRule")
    block = modal[start : start + 700]
    # 递归下钻 user_overrides / role_policies / default_policy 三层
    assert "Object.values(value).some(walk)" in block
    assert "condition || ''" in block
    # 命中时给的是可操作提示，而不是只说"失败"
    assert "启用精细化校验必须至少配一条行级规则" in modal
    assert "关掉上面的开关" in modal

    endpoint = _read(ENDPOINT)
    guard = endpoint[endpoint.index("def _reject_unusable_row_filter_config") :][:1600]
    assert "_has_rules" in guard
    assert "启用数据权限时必须至少配置一条行级规则" in guard


def test_save_error_surfaces_backend_detail() -> None:
    """后端 detail 是用户唯一的线索，不能被吞掉。"""
    modal = _read(MODAL)
    start = modal.index("const handleSavePermConfig")
    block = modal[start : start + 2600]
    assert "e?.response?.data?.detail" in block
    assert "typeof detail === 'string'" in block


def test_save_button_is_locked_while_inflight() -> None:
    """失败时用户容易连点，一屏好几个 toast。"""
    modal = _read(MODAL)
    assert "const saving = ref(false)" in modal
    assert "if (!configuringDataset.value || saving.value) return" in modal
    assert ':disabled="saving || opening"' in modal
    assert "保存中…" in modal


def test_open_reloads_detail_before_editing() -> None:
    """打开时用详情校正行级配置，避免把列表里的半成品写回去。"""
    modal = _read(MODAL)
    start = modal.index("const openConfig")
    block = modal[start : start + 1800]
    # 详情必须比编辑更早到位
    assert "await fetchDatasetColumns(datasetId)" in block
    assert "detail.enable_data_perm" in block
    assert "detail.row_filter_config" in block
    # 拉取期间禁止保存
    assert "opening.value = true" in block
    assert "opening.value = false" in block
    # 详情拉不到就别让用户对着空配置编辑
    assert "读取数据集行级配置失败" in block

    # fetchDatasetColumns 要把详情返回出去，否则上面这些拿不到
    fetch_start = modal.index("const fetchDatasetColumns")
    fetch_block = modal[fetch_start : fetch_start + 900]
    assert "return detail" in fetch_block
    assert "return null" in fetch_block


def test_modal_icons_are_all_imported() -> None:
    """模板里用到的图标必须真的 import，否则渲染成未知标签。

    抽组件时漏了 LockOpenIcon：没启用校验的空态会渲染不出图标，而 vue-tsc 不报错。
    """
    modal = _read(MODAL)
    for icon in (
        "ExclamationTriangleIcon",
        "InformationCircleIcon",
        "LockOpenIcon",
        "MagnifyingGlassIcon",
        "UserIcon",
    ):
        assert f"  {icon}," in modal, f"{icon} 没 import"
