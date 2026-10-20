"""部门编码实时查重的前端契约。

需求：「code 不能重复，添加到页面实时检测是否重复并提示」。

三个容易做错的地方，这里都钉住：

1. **初次加载不能误报**：编辑面板一打开 `form.code` 就是库里已有的编码，
   若响应式 watch 直接触发查重，用户会立刻看到"该编码已被使用"——明明没改过。
   所以用 `baselineCode` 区分"程序回填"与"用户改动"。
2. **必须有防抖与乱序保护**：逐字符请求会把后端打满，且快速修改时旧响应可能
   覆盖新结论（把"可用"盖成"已被使用"）。
3. **提交前必须兜底**：输入期可能因为网络失败而"未判定"，也可能被别人抢先占用；
   只靠输入期提示等于把唯一性交给运气。
"""

from __future__ import annotations

from pathlib import Path

pytestmark = __import__("pytest").mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend/src"

COMPOSABLE = FRONTEND / "composables/useDepartmentCodeAvailability.ts"
INFO_PANEL = FRONTEND / "components/department/DepartmentInfoPanel.vue"
MANAGEMENT = FRONTEND / "views/DepartmentManagement.vue"


def test_composable_queries_backend_authority():
    src = COMPOSABLE.read_text(encoding="utf-8")
    assert "/api/portal/departments/code-available" in src
    assert "exclude_ref" in src, "编辑时必须排除自身，否则永远提示已被使用"
    # 判定口径只认后端：前端不得自己猜规则
    assert "data.reason" in src


def test_composable_has_debounce_and_out_of_order_guard():
    src = COMPOSABLE.read_text(encoding="utf-8")
    assert "checkDebounced" in src
    assert "setTimeout" in src and "clearTimeout" in src, "输入期必须防抖"
    assert "requestSeq" in src, "乱序响应必须丢弃，否则旧结论会覆盖新结论"


def test_composable_does_not_block_when_precheck_fails():
    """预检接口挂了不能挡住提交：后端才是权威兜底。"""
    src = COMPOSABLE.read_text(encoding="utf-8")
    catch_block = src.split("} catch {", 1)[1].split("} finally {", 1)[0]
    assert "return true" in catch_block


def test_info_panel_distinguishes_refill_from_user_edit():
    src = INFO_PANEL.read_text(encoding="utf-8")
    assert "baselineCode" in src
    assert "baselineCode.value = form.code" in src, "回填后要记住基线，作为「未改动」的判据"


def test_info_panel_blocks_save_when_code_taken():
    src = INFO_PANEL.read_text(encoding="utf-8")
    assert "codeAvailable === false" in src
    assert "disabled=\"saving || codeAvailable === false" in src, "编码不可用时保存按钮必须禁用"
    assert "ensureCodeAvailable" in src, "点击保存还要兜底再查一次"


def test_info_panel_shows_inline_feedback():
    src = INFO_PANEL.read_text(encoding="utf-8")
    assert "正在检查编码" in src
    assert "编码可用" in src
    assert "text-red-500" in src


def test_create_dialog_checks_code_and_guards_submit():
    src = MANAGEMENT.read_text(encoding="utf-8")
    assert "useDepartmentCodeAvailability" in src
    assert "@input" in src or "watch(" in src
    assert "ensureCreateCodeAvailable" in src, "创建提交前必须兜底查重"
    assert "resetCreateCodeCheck" in src, "每次打开弹窗要清掉上一次的判定"


def test_create_dialog_explains_pure_digit_rule():
    """纯数字编码会被后端拒绝（与自增 id 冲突），提示要写在输入框旁。"""
    src = MANAGEMENT.read_text(encoding="utf-8")
    assert "纯数字" in src
