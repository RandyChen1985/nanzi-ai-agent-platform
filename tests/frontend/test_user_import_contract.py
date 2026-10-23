"""用户管理「Excel 导入用户」的前端契约（源码级）。

入口藏在用户管理页「更多」菜单里，整个流程是三步弹窗：上传 → 确认 → 账号密码。
这里钉住用户明确要求的几件事，避免后续改版把功能悄悄弄丢：

1. 「更多」里有「导入用户」，且只在有 `element:user:edit` 时显示；
2. 导入页能下载模板、上传 .xlsx，并在确认页支持改角色、勾选删除、补充新增；
3. 角色对不上时在确认页可选择，未选角色不能提交；
4. 结果页回显账号密码，支持复制与下载，且明确提示密码只显示一次。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS_VUE = ROOT / "frontend/src/views/Users.vue"
IMPORT_DIALOG = ROOT / "frontend/src/components/user/UserImportDialog.vue"

pytestmark = pytest.mark.no_infrastructure


def _users_source() -> str:
    return USERS_VUE.read_text(encoding="utf-8")


def _dialog_source() -> str:
    return IMPORT_DIALOG.read_text(encoding="utf-8")


def test_more_menu_has_import_entry_behind_edit_permission():
    source = _users_source()

    assert "批量导入用户" in source
    before = source.split("批量导入用户", 1)[0]
    after = source.split("批量导入用户", 1)[1]
    entry = before.rsplit("<button", 1)[1] + "批量导入用户" + after.split("</button>", 1)[0]
    assert 'v-if="canEditUser"' in entry, "「导入用户」必须受 element:user:edit 控制"
    assert "ArrowUpTrayIcon" in entry


def test_import_dialog_is_mounted_and_refreshes_list():
    source = _users_source()

    assert "import UserImportDialog from" in source
    assert "<UserImportDialog" in source
    assert ':visible="showImportDialog"' in source
    assert '@imported="fetchUsers"' in source


def test_dialog_has_three_steps_and_preview_before_create():
    source = _dialog_source()

    assert 'type Step = "upload" | "confirm" | "result"' in source
    assert "/api/portal/management/users/import/preview" in source
    assert "/api/portal/management/users/import/confirm" in source
    assert "上传文件" in source and "确认数据" in source and "账号密码" in source
    # 上传后进入确认页，不是直接建号
    assert 'step.value = "confirm"' in source


def test_template_download_and_xlsx_upload():
    source = _dialog_source()

    assert "/api/portal/management/users/import/template" in source
    assert 'responseType: "blob"' in source
    assert '"用户导入模板.xlsx"' in source
    assert 'accept=".xlsx"' in source
    assert 'endsWith(".xlsx")' in source


def test_confirm_page_supports_role_delete_and_add():
    source = _dialog_source()

    assert "补充新增" in source
    assert "删除所选" in source
    assert "重新上传" in source
    assert "请选择角色" in source
    assert "导入账号均为普通用户" in source
    # 角色匹配不上时给出可操作提示
    assert "未匹配到内置角色" in source
    # 未选角色 / 行内错误时禁止提交
    assert "row.role_id === null" in source
    assert "blockingSelected > 0" in source


def test_result_page_reveals_passwords_once_with_copy_and_download():
    source = _dialog_source()

    assert "初始密码" in source
    assert "复制全部" in source
    assert "下载 CSV" in source
    assert "密码仅本次显示" in source
    assert "离开本页后无法再次查看明文密码" in source
    assert "text/csv;charset=utf-8" in source
    assert "\\uFEFF" in source, "CSV 需要 BOM，否则 Excel 打开中文乱码"


def test_dialog_resets_when_reopened():
    source = _dialog_source()

    assert "watch(" in source
    assert "if (visible) resetAll();" in source


def test_password_is_only_sent_as_plaintext_in_create_response():
    """前端不得把密码写进 localStorage / sessionStorage / console。"""

    source = _dialog_source()

    for forbidden in ("localStorage", "sessionStorage", "console.log"):
        assert forbidden not in source


def test_existing_account_warning_is_visible_and_explains_next_step():
    """已存在账号不能只在行内标红：确认页顶部要有汇总与操作指引。"""

    source = _dialog_source()

    assert "导入前检查：以下数据不会被导入" in source
    assert "账号已存在" in source
    assert "取消勾选后才能提交" in source
    assert "existedSelected" in source
    assert "不会导入；请取消勾选或修改账号名" in source


def test_confirm_page_offers_random_or_fixed_password():
    source = _dialog_source()

    assert "随机密码（每个账号不同）" in source
    assert "固定密码（所有账号相同）" in source
    assert 'v-model="fixedPassword"' in source
    assert "password_mode: passwordMode.value" in source
    assert "fixed_password: passwordMode.value" in source

    # 固定密码必须有强度校验，且不能包含任一待导入账号名
    assert "fixedPasswordIssue" in source
    assert "固定密码不能包含账号名" in source
    assert "canSubmit" in source


def test_confirm_page_can_send_password_to_user_email():
    source = _dialog_source()

    assert "发送密码到用户邮箱" in source
    assert "send_password_email: sendPasswordEmail.value" in source
    assert "sendEmailRequested" in source
    assert "密码邮件" in source
    assert "已发送" in source
    assert "发送失败" in source

    # 邮件失败不能只说“失败”：必须告诉管理员账号已经建好、密码要另行分发
    assert "账号已创建成功" in source
    assert "复制或下载密码" in source


def test_confirm_page_has_no_identity_switch():
    """导入账号一律普通用户，确认页不应再暴露系统管理员选项。"""

    source = _dialog_source()

    assert 'v-model="row.system_role"' not in source
    assert "身份</th>" not in source
    assert "系统管理员" not in source.split("uploadTips", 1)[0]


def test_email_switch_is_disabled_when_global_mail_service_is_off():
    source = _dialog_source()

    assert "emailServiceAvailable" in source
    assert ':disabled="!emailServiceAvailable"' in source
    assert "平台未开启全局邮件服务" in source
    assert "data.email_service_available === true" in source


def test_excel_import_supports_optional_real_name():
    source = _dialog_source()

    assert "姓名" in source
    assert 'placeholder="姓名（选填）"' in source
    assert "real_name: row.real_name.trim()" in source
    assert "row.real_name || \"\"" in source
    assert '["账号", "姓名", "邮箱", "角色", "初始密码"]' in source


def test_delete_column_is_sticky_and_table_fits_desktop_width():
    """删除按钮不能藏在横向滚动后面：操作列固定在右侧，且列宽总和要收进弹窗。"""

    source = _dialog_source()

    assert 'sticky right-0 z-20 w-11 bg-gray-50' in source
    assert 'sticky right-0 z-10 w-11' in source
    assert ":class=\"rowClass(row) || 'bg-white'\"" in source
    assert 'min-w-[920px]' in source
