from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "frontend/src/views/SystemConfig.vue"


def test_sandbox_policy_options_render_semantic_icons_for_all_execution_modes():
    source = SETTINGS.read_text(encoding="utf-8")

    assert "ComputerDesktopIcon" in source
    assert "CubeIcon" in source
    assert "ServerStackIcon" in source
    assert "CloudIcon" in source
    assert "ServerIcon" in source
    assert "k8s: ServerStackIcon" in source
    assert "getSandboxPolicyIcon" in source
    assert ':is="getSandboxPolicyIcon(opt.value)"' in source
    assert 'aria-hidden="true"' in source


def test_docker_policy_is_available_when_platform_runs_in_docker():
    source = SETTINGS.read_text(encoding="utf-8")

    assert "value: 'docker'" in source
    assert "value: 'k8s'" in source
    assert "disabled: false" in source
    assert ":disabled=\"isConfigItemDisabled(String(category), item) || opt.disabled\"" in source
    assert "showToast('平台后端已经运行在 Docker 容器内，不能启用 docker 沙箱模式', 'warning')" not in source


def test_k8s_sandbox_rbac_guide_and_check_contract():
    source = SETTINGS.read_text(encoding="utf-8")

    # 指引卡片与复制命令
    assert "kubectl apply -f k8s_deploy/sandbox-rbac.example.yaml" in source
    assert "copyK8sRbacCommand" in source
    assert "k8sRbacCommandCopied" in source

    # 校验按钮与 API 调用
    assert "checkK8sRbac" in source
    assert "k8sChecking" in source
    assert "k8sCheckResult" in source
    assert "/api/v1/admin/sandbox/k8s/check-rbac" in source
    assert "校验 K8s 集群与 RBAC 权限" in source

