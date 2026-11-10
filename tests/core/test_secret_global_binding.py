"""全局绑定（bound_by_user_id=''）必须真的能写进去。

这个能力曾经是**死代码**：`center.bind_secret` 只在 `bound_by_user_id is None`
时走全局分支，而唯一的调用点 `create_binding` 永远传 `user_id`。于是：

    管理员在「MCP 服务注册」里给平台服务绑一条系统级凭据
      → 落库成 bound_by_user_id = 管理员 id（他自己的私有绑定）
    普通用户调用同一个服务
      → list_bound_slots 按主体过滤掉，判定「未绑定」
      → 注入失败

表现是「管理员自己测一切正常，别人全用不了」，而管理端界面上那条绑定
看起来好好的。前端还额外假设了它是全局的（`row.owner_scope === 'system'`
就算已绑定），前后端对同一个字段的理解不一致。
"""

from __future__ import annotations

import inspect

import pytest

from app.api.portal.endpoints import secrets as endpoint
from app.services.secrets import center


pytestmark = pytest.mark.no_infrastructure


class TestRequestModelCarriesTheFlag:
    def test_bind_request_has_is_global(self):
        assert "is_global" in endpoint.BindRequest.model_fields

    def test_unbind_request_has_is_global(self):
        assert "is_global" in endpoint.UnbindRequest.model_fields

    def test_defaults_to_personal(self):
        """默认必须是个人绑定：漏传就等于悄悄配了一份全局共享。"""
        assert endpoint.BindRequest.model_fields["is_global"].default is False
        assert endpoint.UnbindRequest.model_fields["is_global"].default is False


class TestEndpointMapsFlagToEmptyString:
    def _source(self, func) -> str:
        return inspect.getsource(func)

    def test_create_passes_none_for_global(self):
        body = self._source(endpoint.create_binding)
        assert "if payload.is_global:" in body
        assert "bound_by: str | None = None" in body
        assert "bound_by_user_id=bound_by" in body

    def test_create_requires_admin_for_global(self):
        body = self._source(endpoint.create_binding)
        start = body.find("if payload.is_global:")
        window = body[start:start + 300]
        assert "_is_admin(user_info)" in window
        assert "403" in window

    def test_delete_mirrors_the_same_rule(self):
        body = self._source(endpoint.delete_binding)
        assert "if payload.is_global:" in body
        assert "bound_by: str | None = None" in body
        assert "_is_admin(user_info)" in body


class TestCenterAcceptsGlobal:
    def test_none_means_global(self):
        body = inspect.getsource(center.bind_secret)
        assert "bound_by_user_id is None" in body
        # 空串才是落库形态（唯一索引里 NULL 互不相等）
        assert 'owner_key = bound_by_user_id or ""' in body

    def test_personal_secret_cannot_be_global(self):
        """个人凭据做成全局没有语义——它不是「平台的东西」。"""
        body = inspect.getsource(center.bind_secret)
        assert "只有系统级凭据可以设为全局绑定" in body


class TestAdminComponentsSendGlobal:
    """管理端的四个组件都必须显式传 is_global。

    这些组件都在系统管理页，绑的是平台共用的服务/工具。不传的话，
    管理员配完只有他自己能用——正是这个 bug 的表现。
    """

    ADMIN_FILES = [
        "frontend/src/components/system/McpServerRegistry.vue",
        "frontend/src/components/system/ToolHeadersEditor.vue",
        "frontend/src/components/system/ToolSystemParamEditor.vue",
        "frontend/src/components/system/ScriptToolWizard.vue",
    ]

    @pytest.mark.parametrize("path", ADMIN_FILES)
    def test_component_sends_is_global(self, path):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / path).read_text(encoding="utf-8")
        # 只数真正的写调用：GET 也在同一路径上，数它会得出错误的期望值
        import re

        writes = len(re.findall(
            r"axios\.(?:post|delete)\(\s*['\"]/api/portal/secrets/bindings", source
        ))
        flags = source.count("is_global: true")
        assert writes > 0, f"{path}: 没找到绑定写入调用"
        assert flags == writes, (
            f"{path}: {writes} 处写入只带了 {flags} 个 is_global —— "
            "漏传的会写成个人绑定，只有管理员自己能用"
        )
