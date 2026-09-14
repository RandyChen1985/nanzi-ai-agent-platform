from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "k8s_deploy"


def test_k8s_secret_example_contains_compatible_default_encryption_key():
    text = (K8S_DIR / "secret.example.yaml").read_text(encoding="utf-8")

    assert (
        'ENCRYPTION_KEY: "KkJgK_d-1Jda9CAp7iGhRDzuXLYZfnid2siBeIC5lqw="' in text
    )


def test_k8s_docs_cover_first_deploy_secrets_data_init_and_upgrade_restart():
    text = (K8S_DIR / "README.md").read_text(encoding="utf-8")

    assert "先按这 9 步做" in text
    assert "https://github.com/RandyChen1985/nanzi-ai-agent-platform/releases" in text
    assert "新环境可以不改" in text
    assert "管理员" in text
    assert "data-init-job.example.yaml" in text
    assert "kubectl apply -f k8s_deploy/secret.yaml" in text
    assert "rollout restart deployment/nanzi-ai-agent" in text
    assert "系统配置或模型管理" in text
    assert "系统配置 → 知识库设置" in text


def test_k8s_data_init_job_only_copies_public_docs_into_the_pvc():
    text = (K8S_DIR / "data-init-job.example.yaml").read_text(encoding="utf-8")

    assert "kind: Job" in text
    assert "claimName: nanzi-ai-agent-data" in text
    assert "cp -a /app/data/docs/. /mnt/data/docs/" in text
    assert "/app/data/uploads" not in text
    assert "/app/data/agent_workspaces" not in text


def test_k8s_default_resources_do_not_apply_secret_or_data_init_job():
    text = (K8S_DIR / "kustomization.yaml").read_text(encoding="utf-8")

    assert "secret.example.yaml" not in text
    assert "secret.yaml" not in text
    assert "data-init-job.example.yaml" not in text


def test_k8s_deployment_binds_the_sandbox_rbac_service_account():
    deployment = (K8S_DIR / "deployment.yaml").read_text(encoding="utf-8")
    service_account = (K8S_DIR / "serviceaccount.yaml").read_text(encoding="utf-8")
    kustomization = (K8S_DIR / "kustomization.yaml").read_text(encoding="utf-8")
    docs = (K8S_DIR / "README.md").read_text(encoding="utf-8")

    assert "serviceAccountName: nanzi-ai-agent-sa" in deployment
    assert "name: nanzi-ai-agent-sa" in service_account
    assert "- serviceaccount.yaml" in kustomization
    assert "默认 Deployment 已绑定" in docs


def test_k8s_docs_include_k3s_single_node_practical_flow():
    text = (K8S_DIR / "README.md").read_text(encoding="utf-8")

    assert "K3s 单机实操" in text
    assert "curl -sfL https://get.k3s.io | sh -" in text
    assert "/etc/rancher/k3s/k3s.yaml" in text
    assert "sudo k3s ctr images import" in text
    assert "local-path" in text
    assert "yunshu-test" in text
    assert "6443" in text
    assert "8472" in text
    assert "K3s 官方快速开始" in text
    assert "failCgroupV1: false" in text
    assert "stat -fc %T /sys/fs/cgroup" in text


def test_k8s_docs_cover_wizard_install_script_and_ops_tools():
    text = (K8S_DIR / "README.md").read_text(encoding="utf-8")

    assert "install.sh" in text
    assert "./install.sh --try" in text
    assert "nanzi-k8s.sh" in text
    # 沙箱命名空间：默认与平台同命名空间，同时保留独立命名空间（强隔离）说明
    assert "sandbox_k8s_namespace" in text
    assert "nanzi-ai-agent" in text
    assert "agent-sandboxes" in text
    assert "upgrade.md" in text
    assert (K8S_DIR / "install.sh").is_file()
    assert (K8S_DIR / "nanzi-k8s.sh").is_file()
    assert (K8S_DIR / "upgrade.md").is_file()


def test_k8s_ops_script_filters_sandbox_listing_by_agentscope_label():
    """沙箱命名空间默认与平台同命名空间，脚本列举沙箱资源时必须按标签过滤。

    否则 `nanzi-k8s.sh sandboxes` / `status` / `health` 会把平台自身的
    Deployment Pod（如 nanzi-ai-agent-xxxx-yyyy）与平台数据卷一并列出，
    被误认成"多出来的沙箱"。
    """
    script = (K8S_DIR / "nanzi-k8s.sh").read_text(encoding="utf-8")

    assert 'SANDBOX_LABEL="app.kubernetes.io/managed-by=agentscope"' in script
    # status / sandboxes / health 三处列举都必须带上标签过滤
    assert script.count('"$SANDBOX_LABEL"') >= 8

    # 不得存在不带过滤的裸列举
    assert 'kubectl get pod -n "$SANDBOX_NAMESPACE" -o wide' not in script
    assert 'kubectl get pvc -n "$SANDBOX_NAMESPACE" -o wide' not in script
    assert 'kubectl get pod,pvc -n "$SANDBOX_NAMESPACE" --no-headers' not in script
    assert 'kubectl get pod,pvc -n "$SANDBOX_NAMESPACE" -o wide' not in script
    assert 'health_get get pods -n "$SANDBOX_NAMESPACE" --no-headers' not in script
    assert 'health_get get pvc -n "$SANDBOX_NAMESPACE" --no-headers' not in script


def test_k8s_ops_script_excludes_sandbox_pods_from_platform_views():
    """平台视角（第 3 节、重启后 Pod 列表、health 平台 Pod 状态）不得混入沙箱 Pod。

    同命名空间下若不加过滤，`status` 第 3 节「NanZi 平台应用资源」会同时列出
    平台 Deployment Pod 与沙箱 Pod（如 as-ws-admin--1），误导运维判断。
    过滤一律采用正向标签 `app.kubernetes.io/name=<Deployment>`（平台 Pod、Service、
    Ingress、平台 PVC 均带此标签），不依赖 `key!=value` 对"无该标签对象"的匹配语义。
    """
    script = (K8S_DIR / "nanzi-k8s.sh").read_text(encoding="utf-8")

    assert 'PLATFORM_APP_LABEL="app.kubernetes.io/name=${DEPLOYMENT}"' in script
    # status 第 3 节、restart-pod / restart-pod-force / restart-all 后的 Pod 列表
    assert 'kubectl get pod,svc,ingress -n "$NAMESPACE" -l "$PLATFORM_APP_LABEL" -o wide' in script
    assert script.count('kubectl get pods -n "$NAMESPACE" -l "$PLATFORM_APP_LABEL" -o wide') >= 3
    # health 的平台 Pod 状态三项检查
    assert script.count('get pods -n "$NAMESPACE" -l "$PLATFORM_APP_LABEL"') >= 3

    # 不得存在不带过滤的平台侧 Pod 列举
    assert 'kubectl get pod,svc,ingress -n "$NAMESPACE" -o wide' not in script
    assert 'kubectl get pods -n "$NAMESPACE" -o wide' not in script
    assert 'health_get get pods -n "$NAMESPACE" --no-headers' not in script


def test_k8s_ops_script_merges_events_when_namespace_shared():
    """沙箱与平台同命名空间时事件是同一份，不应重复打印两个小节。"""
    script = (K8S_DIR / "nanzi-k8s.sh").read_text(encoding="utf-8")

    assert '[ "$SANDBOX_NAMESPACE" = "$NAMESPACE" ]' in script
    assert "命名空间事件" in script
    # 合并分支存在的前提下，两次相同的 events 列举仍保留给"不同命名空间"场景
    assert script.count("kubectl get events -n") >= 2


def test_k8s_ops_script_surfaces_shared_platform_data_volume():
    """过滤后仍须能看到沙箱复用的平台共享数据卷（否则无从确认沙箱用的是哪块盘）。"""
    script = (K8S_DIR / "nanzi-k8s.sh").read_text(encoding="utf-8")

    # 通过平台 Pod 标签识别其挂载的 PVC
    assert 'PLATFORM_APP_LABEL="app.kubernetes.io/name=${DEPLOYMENT}"' in script
    # 标签常量必须在 DEPLOYMENT 之后定义（set -u 下先引用会直接中断脚本）
    assert script.index('DEPLOYMENT="nanzi-ai-agent"') < script.index('PLATFORM_APP_LABEL=')

    # 复用型 helper：定义 + 在 status 与 sandboxes 两处调用
    assert "print_shared_workspace_volume() {" in script
    assert script.count("print_shared_workspace_volume") >= 3
    # 探测失败时的退化路径：按同一平台标签列出平台 PVC
    assert 'kubectl get pvc -n "$SANDBOX_NAMESPACE" -l "$PLATFORM_APP_LABEL" --no-headers' in script
    # 沙箱工作区小节与共享卷小节均需出现在展示中
    assert "共享数据卷" in script
    assert "平台共享数据卷（沙箱通过 subPath 复用）" in script


def test_k8s_build_sandbox_image_script_and_docs_contract():
    """网关预置镜像构建脚本与文档指引契约。"""
    build_script = K8S_DIR / "build-k8s-sandbox-image.sh"
    assert build_script.is_file()
    script_source = build_script.read_text(encoding="utf-8")
    assert "build-k8s-sandbox-image.sh" in script_source
    assert "_mcp_gateway_app.py" in script_source
    assert 'GATEWAY_HOME="/root/.agentscope"' in script_source
    assert "mcp<2.0.0" in script_source
    assert "sandbox_k8s_image" in script_source
    template = K8S_DIR / "sandbox-image" / "_mcp_gateway_app.py"
    assert template.is_file()

    upgrade_text = (K8S_DIR / "upgrade.md").read_text(encoding="utf-8")
    assert "build-k8s-sandbox-image.sh" in upgrade_text
    assert "网关预置镜像" in upgrade_text

    readme_text = (K8S_DIR / "README.md").read_text(encoding="utf-8")
    assert "build-k8s-sandbox-image.sh" in readme_text
    assert "网关预置镜像" in readme_text
    assert "check-sandbox-image" in readme_text
