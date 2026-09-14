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


def test_k8s_workspace_mount_guidance_reflects_current_behaviour():
    """K8s 沙箱挂载说明契约：防止长说明/引导卡片与实际实现漂移。

    历史教训：subPath 从 `agent_workspaces/{user_key}/sandbox` 调整为整个用户
    工作区 `agent_workspaces/{user_key}`、公共文档挂载点由 `/workspace/docs` 调整为
    `/workspace/public/docs`、`sandbox_k8s_existing_pvc` 语义由「留空=独立临时卷」
    调整为「留空=自动共享平台数据卷，none=强制隔离」，三处变更都曾漏改部分文案。
    本契约同时覆盖两处独立文案区块（`【参数作用】` 长说明与 `💡 参数作用与是否必填`
    引导卡片），确保二者与实现一致。
    """
    source = SETTINGS.read_text(encoding="utf-8")

    # 1. subPath 必须指向整个用户工作区根，而不是其下的 sandbox 子目录
    assert "subPath: agent_workspaces/{user_key}</code>" in source
    assert "subPath: agent_workspaces/{user_key}/sandbox" not in source

    # 2. 公共文档挂载点与实际挂载路径一致
    assert "/workspace/public/docs" in source
    assert "/workspace/docs" not in source

    # 3. 留空语义 = 自动探测共享（零配置），并提供 none 哨兵值强制隔离
    assert "自动探测共享模式" in source
    assert "自动探测" in source
    assert "none" in source
    assert "留空自动共享平台数据卷" in source
    # 旧的「留空=独立临时卷」表述不得残留
    assert "留空为独立临时卷" not in source
    assert "留空（即动态创建独立卷模式）时生效" not in source
    # 不得把部署变量硬编码为示例卷名
    assert "nanzi-app-data" not in source
    assert "agent-shared-pvc" not in source

    # 4. 两处独立文案区块都要提到「完整工作区」（与 Docker 沙箱对齐）
    assert source.count("完整工作区") >= 2

