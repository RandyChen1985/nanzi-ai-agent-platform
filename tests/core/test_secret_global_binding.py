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
    """管理端组件必须显式要求全局绑定。

    `ToolRegistry` / `ScriptToolList` 整棵树只挂在 `SystemConfig.vue`，
    绑的是平台共用的工具，不传全局就只有管理员自己能用。
    """

    ADMIN_FILES = [
        "frontend/src/components/system/ToolHeadersEditor.vue",
        "frontend/src/components/system/ToolSystemParamEditor.vue",
        "frontend/src/components/system/ScriptToolWizard.vue",
    ]

    @pytest.mark.parametrize("path", ADMIN_FILES)
    def test_component_sends_is_global(self, path):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / path).read_text(encoding="utf-8")
        imports_re = __import__("re")
        writes = len(imports_re.findall(
            r"axios\.(?:post|delete)\(\s*['\"]/api/portal/secrets/bindings", source
        ))
        flags = source.count("is_global: true")
        assert writes > 0, f"{path}: 没找到绑定写入调用"
        assert flags == writes, (
            f"{path}: {writes} 处写入只带了 {flags} 个 is_global —— "
            "漏传的会写成个人绑定，只有管理员自己能用"
        )


class TestPersonalScopeStaysPersonal:
    """`McpServerRegistry` 同时服务「平台 MCP」和「我的 MCP」，不能硬编码全局。

    这个坑真实踩过：批量补 is_global 时把个人入口一起改成了全局，结果在
    「我的 MCP」里给个人服务的参数选凭据，直接报
    「只有系统级凭据可以设为全局绑定」——因为个人凭据本来就不该全局。

    更糟的分支是：管理员给自己的个人 MCP 选一条系统级凭据，真能建出一条
    全局绑定，把私有服务配成了公共配置。
    """

    PATH = "frontend/src/components/system/McpServerRegistry.vue"

    def _source(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        return (root / self.PATH).read_text(encoding="utf-8")

    def test_does_not_hardcode_global(self):
        assert "is_global: true" not in self._source(), (
            "McpServerRegistry 的绑定必须按 scope 决定，不能写死全局"
        )

    def test_every_write_follows_scope(self):
        import re

        source = self._source()
        writes = len(re.findall(
            r"axios\.(?:post|delete)\(\s*['\"]/api/portal/secrets/bindings", source
        ))
        flags = source.count("is_global: isGlobalBinding.value")
        assert writes > 0
        assert flags == writes, f"{writes} 处写入只带了 {flags} 个 is_global"

    def test_computed_reads_scope_prop(self):
        assert "const isGlobalBinding = computed(() => props.scope === 'global')" in self._source()

    def test_personal_scope_ignores_global_rows(self):
        """个人 scope 下，管理员的全局绑定不该显示成「已绑定」。"""
        assert "isGlobalBinding.value && row.is_global" in self._source()


class TestBindingsEndpointReturnsIsGlobal:
    """`GET /secrets/bindings` 必须回 `is_global`。

    前端靠这个字段区分「全局绑定」和「我绑的」。少了它，`!(isGlobalBinding && 
    row.is_global)` 恒为真，管理端编辑平台 MCP 时**全局绑定被整行过滤掉**：
    界面显示「未选」、连「解除绑定」按钮都不出现；而规则绑定（`secret_id` 为空）
    在这条路上也一样消失。凭据详情接口一直有这个字段，两边口径必须一致。
    """

    def test_list_endpoint_returns_is_global(self):
        body = inspect.getsource(endpoint.get_bindings)
        assert '"is_global": not binding.bound_by_user_id' in body

    def test_same_shape_as_detail_endpoint(self):
        """同一个字段名、同一个算法，否则前端得写两套判断。"""
        detail = inspect.getsource(endpoint.get_secret)
        assert '"is_global": not b.bound_by_user_id' in detail

    @pytest.mark.parametrize(
        "path",
        [
            "frontend/src/components/system/ToolSystemParamEditor.vue",
            "frontend/src/components/system/ToolHeadersEditor.vue",
        ],
    )
    def test_components_judge_global_by_is_global(self, path):
        """不能再用 `owner_scope === 'system'` 代理判断。

        规则行没有 `secret_id`，outerjoin 之后 `owner_scope` 是 null——用它会
        把管理员配的全局规则整条丢掉，表现是「重新打开工具时规则显示未配」。
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / path).read_text(encoding="utf-8")
        assert "row.owner_scope !== 'system'" not in source
        assert "!row.bound_by_me && !row.is_global" in source
