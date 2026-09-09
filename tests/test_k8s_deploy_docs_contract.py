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
    assert "agent-sandboxes" in text
    assert "upgrade.md" in text
    assert (K8S_DIR / "install.sh").is_file()
    assert (K8S_DIR / "nanzi-k8s.sh").is_file()
    assert (K8S_DIR / "upgrade.md").is_file()
