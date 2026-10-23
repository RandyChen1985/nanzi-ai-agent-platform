"""用户 Excel 导入接口的契约与逐行校验行为（不连库）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.portal.endpoints.user_import import _preview_item
from app.services.user_import_service import ParsedImportRow

pytestmark = pytest.mark.no_infrastructure

ENDPOINT_PY = (
    Path(__file__).resolve().parents[3] / "app/api/portal/endpoints/user_import.py"
)


def _source() -> str:
    return ENDPOINT_PY.read_text(encoding="utf-8")


def _roles():
    return [
        SimpleNamespace(id=1, code="default", name="普通用户", description=""),
        SimpleNamespace(id=2, code="analyst", name="分析师", description=""),
    ]


def _preview(
    role_input="analyst",
    email="alice@example.com",
    name="alice",
    real_name="爱丽丝",
    **kwargs,
):
    roles = kwargs.pop("roles", _roles())
    default_role = kwargs.pop("default_role", roles[0])
    return _preview_item(
        row=ParsedImportRow(
            row_number=2,
            user_name=name,
            real_name=real_name,
            email=email,
            role_input=role_input,
        ),
        roles=roles,
        default_role=default_role,
        existing_names=kwargs.pop("existing_names", set()),
        existing_emails=kwargs.pop("existing_emails", set()),
        name_counts=kwargs.pop("name_counts", {"alice": 1}),
    )


# --- 路由与权限 ---


def test_import_endpoints_are_registered_with_the_same_permission():
    source = _source()

    for path in (
        '@router.get("/users/import/template"',
        '@router.post("/users/import/preview"',
        '@router.post("/users/import/check"',
        '@router.post("/users/import/confirm"',
    ):
        assert path in source, f"缺少导入端点 {path}"

    assert source.count('require_permission("element", "element:user:edit")') == 4


def test_router_is_registered_before_management_dynamic_routes():
    """静态 /users/import/* 必须先于 /management 的动态用户路由注册。"""

    api_py = Path(__file__).resolve().parents[3] / "app/api/portal/api.py"
    source = api_py.read_text(encoding="utf-8")

    assert "user_import.router" in source
    assert source.index("user_import.router") < source.index("management.router")


def test_preview_rejects_non_xlsx_and_oversized_files():
    source = _source()

    assert 'endswith(".xlsx")' in source
    assert "MAX_IMPORT_BYTES" in source
    assert "文件超过 2MB" in source


def test_confirm_does_not_log_or_store_plaintext_password():
    source = _source()

    assert '"password": password' in source, "结果页需要回显一次性初始密码"
    assert "logger.info" not in source, "明文密码不得进入日志"
    # 建号只走 AuthService 唯一入口：密码哈希与角色关系在那里随建号一次提交
    assert "AuthService.generate_api_key(" in source
    assert "password=password" in source
    assert "get_password_hash" not in source
    # 不允许再出现第二个直接构造 User 的建号实现
    assert "User(" not in source


# --- 逐行预览 ---


def test_preview_matches_role_and_allows_import():
    item = _preview()

    assert item["role_id"] == 2
    assert item["role_matched"] is True
    assert item["system_role"] == "user", "Excel 不能直接授予系统管理员身份"
    assert item["importable"] is True
    assert item["issues"] == []


def test_preview_flags_unmatched_role_and_blocks_import():
    item = _preview(role_input="不存在的角色")

    assert item["role_id"] is None
    assert item["role_matched"] is False
    assert item["importable"] is False
    assert any("未匹配到内置角色" in issue for issue in item["issues"])


def test_preview_uses_default_role_when_role_cell_is_empty():
    item = _preview(role_input="")

    assert item["role_id"] == 1
    assert item["role_from_default"] is True
    assert item["importable"] is True


def test_preview_blocks_existing_username_or_taken_email():
    item = _preview(existing_names={"alice"}, existing_emails={"alice@example.com"})

    assert item["existed"] is True
    assert item["email_taken"] is True
    assert item["importable"] is False
    assert any("账号已存在" in issue for issue in item["issues"])
    assert any("邮箱已被" in issue for issue in item["issues"])


def test_preview_flags_duplicate_rows_inside_the_file():
    item = _preview(name_counts={"alice": 2})

    assert item["importable"] is False
    assert any("文件内账号重复" in issue for issue in item["issues"])


def test_preview_rejects_invalid_and_missing_email():
    invalid = _preview(email="not-an-email")
    missing = _preview(email="")

    assert invalid["importable"] is False
    assert any("邮箱格式不正确" in issue for issue in invalid["issues"])
    assert missing["importable"] is False
    assert any("邮箱不能为空" in issue for issue in missing["issues"])


def test_preview_rejects_blank_or_spaced_username():
    blank = _preview(name="")
    spaced = _preview(name="a lice")

    assert blank["importable"] is False
    assert any("账号不能为空" in issue for issue in blank["issues"])
    assert spaced["importable"] is False
    assert any("不能包含空格" in issue for issue in spaced["issues"])


def test_generate_api_key_supports_atomic_import_initialization():
    """导入建号复用唯一入口，密码与角色关系必须随建号一次提交。"""

    auth_py = Path(__file__).resolve().parents[3] / "app/services/auth_service.py"
    source = auth_py.read_text(encoding="utf-8")
    signature = source.split("async def generate_api_key(", 1)[1].split(") -> str:", 1)[0]
    body = source.split("async def generate_api_key(", 1)[1].split(
        "async def get_api_key", 1
    )[0]

    assert "password: Optional[str] = None" in signature
    assert "business_role_ids: Optional[List[int]] = None" in signature
    assert "await asyncio.to_thread(" in body, "bcrypt 不应阻塞事件循环"
    assert "await session.flush()" in body, "角色关系需要先拿到自增主键"
    assert "UserRoleRelation(user_id=new_user.id" in body


def test_preview_existing_account_says_it_will_not_be_imported():
    """已存在账号必须明确提示「不会导入」，不能看起来像静默跳过。"""

    item = _preview(existing_names={"alice"})

    assert item["existed"] is True
    assert item["importable"] is False
    assert any(
        "账号已存在" in issue and "不会导入" in issue and "取消勾选" in issue
        for issue in item["issues"]
    )

    source = _source()
    assert "账号已存在，未导入" in source
    assert "邮箱已被其他账号占用，未导入" in source


def test_confirm_supports_fixed_password_and_optional_password_email():
    """确认页两个选项必须有后端契约：固定密码复用前先整体校验，邮件发送失败不丢账号。"""

    source = _source()

    assert "password_mode" in source
    assert "fixed_password" in source
    assert "send_password_email" in source
    assert "validate_fixed_password" in source
    assert "_deliver_credential_mails" in source
    assert "build_account_created_mail" in source
    assert '"email_sent": False' in source
    assert '"email_error": None' in source

    # 邮件失败只标注，不能回滚已创建账号
    assert "不能因为邮件服务没配置就回滚" in source
    assert "平台邮件服务未启用或配置不完整" in source


def test_fixed_password_is_validated_before_creating_any_account():
    """固定密码不适用时必须整体 400，不能建到一半才失败。"""

    source = _source()
    marker = "固定密码包含某个账号名时，不能建到一半才失败"
    assert marker in source

    before = source.split("@router.post(\"/users/import/confirm\"", 1)[1]
    assert before.index("validate_fixed_password") < before.index("\n    for row in request.rows:")


def test_credential_mail_delivery_isolates_per_row_failures(monkeypatch):
    """一封邮件失败不能影响其他行，也不能抛异常打断已经创建的账号。"""

    from app.api.portal.endpoints.user_import import _deliver_credential_mails
    from app.services.email_delivery_service import EmailDeliveryService

    sent_to = []

    def fake_send(settings, to, subject, body):
        if to[0] == "bad@example.com":
            return False, "mailbox unavailable"
        sent_to.append((to[0], subject, body))
        return True, "ok"

    monkeypatch.setattr(EmailDeliveryService, "send_mail", staticmethod(fake_send))

    jobs = [
        {
            "item": {"user_name": "alice", "email_sent": False, "email_error": None},
            "email": "alice@example.com",
            "password": "Abc!12345678",
        },
        {
            "item": {"user_name": "bob", "email_sent": False, "email_error": None},
            "email": "bad@example.com",
            "password": "Xyz!12345678",
        },
    ]

    asyncio.run(_deliver_credential_mails(SimpleNamespace(sender_name="NanZi"), jobs))

    assert jobs[0]["item"]["email_sent"] is True
    assert jobs[0]["item"]["email_error"] is None
    assert jobs[1]["item"]["email_sent"] is False
    assert "mailbox unavailable" in jobs[1]["item"]["email_error"]
    assert [target for target, _, _ in sent_to] == ["alice@example.com"]

    # 邮件正文必须真的包含该用户的账号与密码，否则「自动发送密码」名不副实
    _, _, body = sent_to[0]
    assert "alice" in body and "Abc!12345678" in body


def test_preview_exposes_global_mail_availability():
    """确认页要在开关旁提示并禁用，不能等提交后才在结果页报发送失败。"""

    source = _source()

    assert "email_service_available" in source
    assert "load_global_smtp_settings" in source


def test_confirm_request_does_not_accept_system_role():
    """导入账号固定普通用户，不能通过请求体传入 admin。"""

    source = _source()

    assert "system_role: str" not in source.split("class ImportConfirmRequest", 1)[1].split("def _role_payload", 1)[0]
    assert 'system_role = "user"' in source
    assert 'role=system_role' in source


def test_preview_returns_real_name_and_flags_too_long_name():
    item = _preview()
    assert item["real_name"] == "爱丽丝"
    assert item["importable"] is True

    too_long = _preview(real_name="名" * 51)
    assert too_long["importable"] is False
    assert any("姓名长度" in issue for issue in too_long["issues"])


def test_confirm_passes_real_name_to_the_single_creation_entry():
    source = _source()

    assert "real_name: Optional[str] = None" in source
    assert "real_name=real_name or None" in source
    assert '"real_name": user.real_name' in source


def test_confirm_page_recheck_endpoint_checks_edited_names_and_emails():
    """编辑账号/邮箱后必须能实时查库，不能等 confirm 才报占用。"""

    source = _source()

    assert '@router.post("/users/import/check"' in source
    assert "existing_names = await _existing_names" in source
    assert "normalized_email = AuthService.normalize_email" in source
    assert '"existed": bool(name) and name.lower() in existing_names' in source
    assert '"email_taken": bool(normalized_email)' in source
