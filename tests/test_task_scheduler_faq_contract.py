from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.no_infrastructure
def test_task_scheduler_faq_documents_cover_deployment_and_retry_contract():
    # FAQ.md 现已收敛到仓库根，data/docs/ 下不再保留副本，只校验实际存在的文件
    faq_paths = [
        path
        for path in (REPO_ROOT / "FAQ.md", REPO_ROOT / "data/docs/FAQ.md")
        if path.exists()
    ]
    assert faq_paths, "未找到 FAQ.md（仓库根或 data/docs/ 下）"
    required_faq_markers = (
        "3.7.4 多节点部署与调度器节点开关",
        "3.7.5 定时任务失败重试策略",
        "执行失败策略",
        "立即执行",
        "不会自动重试",
        "每 30 秒",
        "TASK_SCHEDULER_ENABLED",
    )

    for faq_path in faq_paths:
        content = faq_path.read_text(encoding="utf-8")
        for marker in required_faq_markers:
            assert marker in content, f"{faq_path} 缺少 FAQ 说明：{marker}"


@pytest.mark.no_infrastructure
def test_install_guide_documents_scheduler_node_selection():
    content = (REPO_ROOT / "HOW_TO_INSTALL.md").read_text(encoding="utf-8")

    assert "TASK_SCHEDULER_ENABLED" in content
    assert "多节点只给一个节点 `true`" in content
    assert "其他 API 节点 `false`" in content
    assert "滚动发布" in content
