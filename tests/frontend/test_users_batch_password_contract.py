"""用户管理「批量修改密码」的前后端契约。

批量改密最容易退化的三件事，这里逐条钉住：

1. **等保规则只有一份实现**。前端必须复用 `PasswordResetDialog`（内部调
   `checkPasswordPolicy`），不许在 Users.vue 里再写一套裸输入框——否则用户看到
   的规则说明与真实校验会分叉，管理员会以为批量这档更宽松。
2. **「不能包含用户名」必须逐用户判定**。批量用的是同一个密码，包含 alice 对
   alice 不合规、对 bob 未必；后端要把撞上的那个人单独失败并点名，其余照常。
3. **改密要吊销会话、跳过自己**。不吊销等于没改；不跳过自己，管理员一点确认就
   把自己踢下线。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW_PATH = "frontend/src/views/Users.vue"
DIALOG_PATH = "frontend/src/components/PasswordResetDialog.vue"
POLICY_PATH = "frontend/src/utils/passwordPolicy.ts"
BACKEND_PATH = "app/api/portal/endpoints/management.py"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_users_view_exposes_batch_password_entry():
    view = _source(VIEW_PATH)

    assert "批量修改密码" in view
    assert "openBatchPasswordDialog" in view
    assert "batchSettingPassword" in view
    # 与批量启停共用同一个选择态，不另起一套勾选
    assert "selectedUserIds" in view
    assert "selectedLoginNames" in view


def test_users_view_calls_batch_password_endpoint():
    view = _source(VIEW_PATH)

    assert "/api/portal/management/users/batch-set-password" in view
    assert "executeBatchSetPassword" in view
    assert "user_ids: ids" in view
    assert "password }" in view


def test_batch_password_reuses_the_single_policy_dialog():
    view = _source(VIEW_PATH)
    dialog = _source(DIALOG_PATH)
    policy = _source(POLICY_PATH)

    # 同一个组件被单用户与批量两处使用，且批量把全部用户名传进去
    assert view.count("<PasswordResetDialog") == 2
    assert ':login-names="selectedLoginNames"' in view

    # 组件支持多用户名判定，且仍然只有一个规则函数
    assert "loginNames?: string[]" in dialog
    assert "checkPasswordPolicy(password.value)" in dialog
    assert "不能包含用户名（" in dialog
    assert "export function checkPasswordPolicy" in policy
    # Users.vue 不许自己重写复杂度规则
    assert "checkPasswordPolicy" not in view


def test_batch_password_result_surfaces_failures_by_name():
    view = _source(VIEW_PATH)

    assert "showBatchPasswordResultDialog" in view
    assert "batchPasswordResult.failed" in view
    assert "密码不符合其个人等保校验" in view
    assert "已跳过当前登录账号" in view
    # 失败明细要给出「换密码重试」的下一步，否则管理员不知道怎么办
    assert "换一个密码后勾选这些人重试即可" in view


def test_backend_batch_endpoint_validates_per_user_and_revokes_sessions():
    backend = _source(BACKEND_PATH)

    assert "async def batch_set_user_password(" in backend
    assert '"/users/batch-set-password"' in backend
    # 权限与单用户改密一致
    assert 'require_permission("element", "element:user:edit")' in backend
    # 逐用户校验（username=user.user_name），而不是只做一次通用校验
    batch_fn = backend.split("async def batch_set_user_password(")[1].split("async def ")[0]
    assert "username=user.user_name" in batch_fn
    # 改密后吊销会话 + 结果里带 skipped_self / failed
    assert "revoke_sessions_for_user(uid)" in backend
    assert '"skipped_self": skipped_self' in backend
    assert '"failed": failed' in backend
