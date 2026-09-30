import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_uses_python_311_or_newer_for_agentscope():
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in dockerfile or "FROM python:3.12-slim" in dockerfile


def test_requirements_declares_agentscope_runtime_extras():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    # 保留 runtime extras，且必须精确锁定版本：无上限会导致镜像重建时静默拉取
    # 当时的最新正式版，造成本地 .venv 与生产镜像版本漂移
    assert re.search(
        r"^agentscope\[service,storage,workspace\]==\d+\.\d+\.\d+$",
        requirements,
        re.MULTILINE,
    ), "requirements.txt 必须声明 agentscope runtime extras 并精确锁定版本"


def test_checklist_declares_history_slimming_policy():
    """校验清单的「历史记录定期瘦身」策略声明。

    468cd286 起 CHECKLIST 只保留近期交付记录、历史条目定期清理，完整变更多
    追溯 Git。原先断言「AgentScope 全量运行时替换」等具体历史条目必须存在，
    与瘦身策略直接冲突（清单将永远无法清理），故改为校验策略声明本身。
    """
    checklist = (ROOT / "tests" / "CHECKLIST.md").read_text(encoding="utf-8")

    # 策略声明必须在清单顶部可读：解释历史条目为何会消失，避免被误判成记录缺失
    assert "历史已稳定的旧版本记录定期清理瘦身" in checklist
    assert "完整变更可追溯 Git 历史" in checklist
    # 清单仍是结构化的交付记录表
    assert "| 特性 / 修复项 |" in checklist
    assert "| 交付日期 |" in checklist
