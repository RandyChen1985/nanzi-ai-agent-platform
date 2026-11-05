"""read_skill_instruction 的读取上限与截断可见性契约。

背景：技能包导入侧允许单文件 10MB，工具读取侧上限 262144 字符，而工具结果进入
上下文前统一被 ``truncate_for_context`` 砍到 64Ki 字符。三层上限互不对齐，且
``SKILL.md`` 分支截断时**不打任何标记**（只有 ``path`` 资源分支标「已截断」），
于是模型会以为读到的就是全文，日志里也看不出截断。

本文件锁定第一批修复的行为：

1. 未超限：返回头标注 SKILL.md 总字符数，且不出现任何截断标记。
2. 超限：同时保留开头与结尾（技能尾部的禁止事项/输出格式最容易被丢掉），
   中间显式标注省略量，且整段返回不超过上下文侧 64Ki 字符上限。
3. 资源文件分支：超限同样必须显式标注（不允许静默截断）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from app.services.ai.tools.system_executive_tools import read_skill_instruction

pytestmark = pytest.mark.no_infrastructure

# 上下文侧硬上限（app/services/ai/runtime/agentscope/stream_reconcile.py 的
# DEFAULT_TOOL_OUTPUT_MAX_LEN = 64 * 1024）。工具返回必须低于它，否则会被再次截断。
CONTEXT_OUTPUT_LIMIT = 64 * 1024


@pytest.fixture()
def skills_root(tmp_path: Path):
    """把平台技能库指向临时目录，并让权限侧禁用集合为空。"""
    root = tmp_path / "skills"
    root.mkdir()
    with patch("app.core.config.Settings.SKILLS_DIR", new_callable=PropertyMock) as mock_dir:
        mock_dir.return_value = str(root)
        with patch(
            "app.services.ai.skills.policy.get_forbidden_skill_ids",
            new=AsyncMock(return_value=set()),
        ):
            yield root


def _write_skill(root: Path, skill_id: str, body: str) -> Path:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {skill_id}\ndescription: 演示技能\nenabled: true\n---\n{body}",
        encoding="utf-8",
    )
    return skill_md


@pytest.mark.asyncio
async def test_small_skill_reports_total_chars_without_truncation(skills_root: Path):
    """未超限：报出总字符数，且没有任何截断提示。"""
    skill_md = _write_skill(skills_root, "small", "# 流程\n\n第一步：查用户\n")
    total_chars = len(skill_md.read_text(encoding="utf-8"))

    out = await read_skill_instruction.ainvoke({"skill_id": "small"})

    assert "技能读取成功" in out
    assert "第一步：查用户" in out
    assert f"共 {total_chars} 字符" in out
    assert "省略" not in out
    assert "截断" not in out


@pytest.mark.asyncio
async def test_oversized_skill_keeps_head_and_tail(skills_root: Path):
    """超限：开头与结尾都要在，中间显式标注省略，且不超过上下文上限。"""
    head_marker = "HEAD_MARKER_开头必须有"
    tail_marker = "TAIL_MARKER_末尾禁止事项必须保留"
    # 明显超过任何合理单次上限，确保必然触发截断
    filler = "x" * 200_000
    _write_skill(skills_root, "large", f"{head_marker}\n{filler}\n{tail_marker}")

    out = await read_skill_instruction.ainvoke({"skill_id": "large"})

    assert head_marker in out, "开头内容必须保留"
    assert tail_marker in out, "结尾内容必须保留（技能尾部常放禁止事项/输出格式）"
    assert "省略" in out, "中间必须有显式省略标注"
    assert len(out) <= CONTEXT_OUTPUT_LIMIT, "返回长度必须低于上下文侧 64Ki 上限"


@pytest.mark.asyncio
async def test_resource_file_truncation_is_labelled(skills_root: Path):
    """资源文件超限同样必须显式标注，不允许静默截断。"""
    _write_skill(skills_root, "res", "# 说明\n")
    ref_dir = skills_root / "res" / "references"
    ref_dir.mkdir(parents=True)
    (ref_dir / "big.md").write_text("y" * 200_000, encoding="utf-8")

    out = await read_skill_instruction.ainvoke(
        {"skill_id": "res", "path": "references/big.md"}
    )

    assert "技能读取成功" in out
    assert "省略" in out or "已截断" in out
    assert len(out) <= CONTEXT_OUTPUT_LIMIT
