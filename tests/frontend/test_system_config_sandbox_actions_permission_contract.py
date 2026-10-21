"""系统配置页沙箱管理动作的权限门控契约（前后端同码）。

沙箱管理动作——Docker 镜像预构建 / 状态查询、E2B 与 SSH 连通性测试、K8s RBAC 探测——
会改变或探测全局沙箱运行环境，后端与「保存系统配置」同权：
`element:system:config_save`（admin 由 `require_permission` 直通）。

前端对应入口：无该权限时隐藏，并让处理函数在发请求前短路，避免输入框自动触发的静默 403。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"
SANDBOX_API = ROOT / "app/api/v1/endpoints/sandbox.py"


def _source() -> str:
    return SYSTEM_CONFIG.read_text(encoding="utf-8")


def test_entries_are_hidden_without_config_save():
    source = _source()

    assert (
        'v-if="canSave" class="flex flex-wrap items-center justify-between gap-2 '
        'pt-1 border-t border-sky-100/80"' in source
    ), "K8s RBAC 校验操作栏（说明 + 按钮）必须整块按 canSave 隐藏"

    assert (
        'v-if="!isConfigItemDisabled(String(category), item)" '
        'class="mt-3 flex flex-wrap items-center gap-3"' in source
    ), "Docker 镜像「刷新状态 / 预构建镜像」所在操作栏由 isConfigItemDisabled（canSave）控制"

    assert 'v-if="canSave && item.key === \'sandbox_e2b_timeout_seconds\'"' in source, (
        "E2B 沙箱「测试连接」必须按 canSave 隐藏"
    )
    assert 'v-else-if="canSave && item.key === \'sandbox_ssh_remote_workdir\'"' in source, (
        "SSH 沙箱「测试连接」必须按 canSave 隐藏"
    )


def test_handlers_short_circuit_before_calling_the_endpoint():
    source = _source()

    calls = (
        ("refreshDockerPrebuildStatus", "/api/v1/admin/sandbox/docker/prebuild-status"),
        ("executeDockerPrebuild", "/api/v1/admin/sandbox/docker/prebuild/stream"),
        ("testSandboxConnection", "/api/v1/admin/sandbox/${policy}/test-connection"),
        ("checkK8sRbac", "/api/v1/admin/sandbox/k8s/check-rbac"),
    )

    for function_name, endpoint in calls:
        start = source.index(f"const {function_name} = async")
        call = source.index(endpoint)
        assert call > start, f"{function_name} 与 {endpoint} 的上下文定位失败"
        assert "!canSave" in source[start:call], (
            f"{function_name} 必须在请求 {endpoint} 前判断 canSave，"
            "否则无权限时（尤其输入框自动触发）仍会打出 403"
        )


def test_backend_uses_the_same_permission_code_for_sandbox_manage_actions():
    backend = SANDBOX_API.read_text(encoding="utf-8")

    assert 'SANDBOX_MANAGE_PERMISSION = require_permission("element", "element:system:config_save")' in backend
    assert "_require_admin" not in backend, (
        "沙箱管理动作不再按角色判定；统一走 element:system:config_save（admin 由依赖直通）"
    )

    handlers = (
        "get_docker_prebuild_status",
        "trigger_docker_prebuild",
        "stream_docker_prebuild",
        "test_sandbox_connection",
        "check_k8s_rbac_endpoint",
    )
    for handler in handlers:
        start = backend.index(f"async def {handler}(")
        signature_end = backend.index("):", start)
        assert "Depends(SANDBOX_MANAGE_PERMISSION)" in backend[start:signature_end], (
            f"{handler} 必须使用 SANDBOX_MANAGE_PERMISSION（element:system:config_save）"
        )
