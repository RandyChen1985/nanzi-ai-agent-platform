from pathlib import Path


SKILLS_VIEW = Path("frontend/src/views/SkillsManagement.vue")
# 按钮文案已迁到可测的纯函数模块（`REVOKED` 要说「重新提交上架」，
# 三元表达式表达不了），视图只负责渲染 `skillPublicationActionLabel()` 的结果。
PUBLICATION_COPY = Path("frontend/src/utils/skillPublication.ts")


def _source() -> str:
    return SKILLS_VIEW.read_text(encoding="utf-8")


def _copy_source() -> str:
    return PUBLICATION_COPY.read_text(encoding="utf-8")


def test_personal_skill_publication_contract_is_visible():
    source = _source()
    assert "/api/portal/skills/personal/" in source
    assert "/publication-requests" in source
    assert "publication-requests/withdraw" in source
    assert "撤销审核" in source
    assert "skillPublicationActionLabel(" in source
    assert "提交为平台技能" in _copy_source()
    assert "提交新版本" in _copy_source()
    assert "重新提交上架" in _copy_source()
    assert "publication_status" in source


def test_admin_review_contract_is_scoped_to_platform_workbench():
    source = _source()
    assert 'id="tab-skill-publication-review"' in source
    assert "待审核" in source
    assert "publicationQueue.length" in source
    assert "发布审核" in source
    assert "approve" in source
    assert "reject" in source
    assert "个人" in source
    assert "平台" in source
    assert "!personalOnly" in source
