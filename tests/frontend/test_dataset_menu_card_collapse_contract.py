from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_card_collapse_wraps_questions_related_data_and_followups():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue").read_text(encoding="utf-8")
    collapse_start = source.index("<!-- 可折叠卡片主体内容区")
    questions_start = source.index("<!-- You Can Ask Section -->", collapse_start)
    related_start = source.index("<!-- Related Data Section -->", collapse_start)
    followups_start = source.index("<!-- Follow-ups Section -->", collapse_start)
    collapse_end = source.index("<!-- /可折叠卡片主体内容区 -->", collapse_start)

    assert questions_start < related_start < followups_start
    assert followups_start < collapse_end


def test_card_collapse_body_and_card_container_are_both_closed_before_article_ends():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue").read_text(encoding="utf-8")
    collapse_end = source.index("<!-- /可折叠卡片主体内容区 -->")
    article_end = source.index("</article>", collapse_end)

    assert source[collapse_end:article_end].count("</div>") == 1
