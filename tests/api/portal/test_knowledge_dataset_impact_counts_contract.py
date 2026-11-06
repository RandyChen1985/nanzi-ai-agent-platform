"""知识库列表「引用 / 授权」两列的契约。

这两个数字回答「动这个库会波及谁」：
- 引用 = 智能体 engine_config.dataset_ids 里选了这个库，删库/删文档会让它检索不到；
- 授权 = ResourcePermission 里给了角色/用户访问权，收窄可见范围会让这些人失去入口。

列表一次要展示十几个库，所以必须批量算：
- 引用复用 KnowledgeReferenceService.agents_by_dataset（一次扫智能体表再分组）；
- 授权用一条 group by 聚合，禁止按库逐个查。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "app/api/portal/endpoints/ragflow.py"
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def source() -> str:
    return SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _func_block(source: str, func: str) -> str:
    start = source.index(f"async def {func}(")
    end = source.find("\nasync def ", start + 1)
    return source[start: end if end != -1 else len(source)]


def test_grant_counts_use_single_group_by(source: str) -> None:
    """一次 group by 算全部库，不能在循环里按库查。"""
    block = _func_block(source, "_attach_grant_counts")
    assert "group_by(ResourcePermission.resource_id)" in block
    assert "func.count(func.distinct(ResourcePermission.user_id))" in block
    assert "func.count(func.distinct(ResourcePermission.role_id))" in block
    # 只统计启用中的授权，撤销过的不该计入影响面
    assert "ResourcePermission.enabled == True" in block
    assert "for item in items:\n        dataset_id" in block or "for item in items:" in block


def test_grant_counts_are_attached_to_list_items(source: str) -> None:
    """列表接口必须真的调用它，否则前端拿不到这两个字段。"""
    listing = _func_block(source, "list_ragflow_datasets")
    assert "await _attach_grant_counts(db, merged)" in listing


def test_grant_counts_default_to_zero_for_empty_input(source: str) -> None:
    """没有 dataset_id 时也要把字段补齐，前端不用到处判 undefined。"""
    block = _func_block(source, "_attach_grant_counts")
    assert 'item["grant_user_count"] = 0' in block
    assert 'item["grant_role_count"] = 0' in block


def test_list_columns_show_reference_and_grant(page: str) -> None:
    """列表表头与行都要有这几列，且空值显示为「—」而不是 0。

    列宽串会随后续加列（如「编辑权限」78px）而变，断言用「表头与行必须一致」
    这个不变式，而不是把某一版列宽写死——写死只会让每次加列都假失败，
    从而被顺手改成两个不同的串。
    """
    assert "<span>引用</span>" in page
    assert "<span>授权</span>" in page

    import re

    patterns = re.findall(r"grid-cols-\[18px_[^\]]+\]", page)
    assert len(patterns) >= 2, "列表表头与行都应使用同一套列宽"
    assert len(set(patterns)) == 1, f"表头与行列宽不一致：{sorted(set(patterns))}"


def test_grant_text_and_hints_explain_the_numbers(page: str) -> None:
    """数字本身没含义：用户/角色混合用 `3+2`，只有一个维度时写明单位。"""
    assert "const libraryGrantText = (ds: KnowledgeBase): string => {" in page
    assert "return `${users}+${roles}`" in page
    assert "return users ? `${users} 人` : `${roles} 角色`" in page
    assert "const libraryRefHint = (ds: KnowledgeBase): string => {" in page
    assert "const libraryGrantHint = (ds: KnowledgeBase): string => {" in page
