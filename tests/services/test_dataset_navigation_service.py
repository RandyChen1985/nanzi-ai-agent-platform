from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.dataset_navigation_service import (
    DatasetNavigationService,
    count_datasets_in_menu,
    menu_has_authorized_datasets,
)


SAMPLE_MENU = """Available Datasets (Look for Table terms to find relevant data):
- Dataset: ai_agent_meta [auto-import]
  Display Name: 智能体元数据
  Description: Imported via Smart Wizard
  Includes Tables: 智能体访问日志, 智能体对话历史, 智能体执行链路日志, AI模型定义
  Table Details:
    - 智能体访问日志: 记录 API 访问
    - 智能体对话历史: 用户对话存档
    - 智能体执行链路日志: Trace 执行记录
    - AI模型定义: 模型配置信息

- Dataset: sync_test_e76b6f
  Description: No description
  Includes Tables: 测试表
  Table Details:
    - 测试表

"""


@pytest.mark.no_infrastructure
def test_count_datasets_in_menu():
    assert count_datasets_in_menu(SAMPLE_MENU) == 2
    assert count_datasets_in_menu("No authorized datasets available") == 0


@pytest.mark.no_infrastructure
def test_menu_has_authorized_datasets():
    assert menu_has_authorized_datasets(SAMPLE_MENU) is True
    assert menu_has_authorized_datasets("Available Datasets\n  (No authorized datasets available)") is False


@pytest.mark.no_infrastructure
def test_dataset_navigation_generation_prompt_uses_dataset_menu():
    prompt = DataQueryPrompts.dataset_navigation_generation_prompt(SAMPLE_MENU)
    assert "ai_agent_meta" in prompt
    assert "业务场景卡片" in prompt
    assert "我的数据门户" in prompt
    assert "示例问题" in prompt
    assert "继续追问" in prompt
    assert "{dataset_menu}" in prompt


@pytest.mark.no_infrastructure
def test_parse_dataset_blocks_captures_table_descriptions():
    blocks = DataQueryPrompts._parse_dataset_blocks(SAMPLE_MENU)
    assert len(blocks) == 2
    assert blocks[0]["name"] == "ai_agent_meta"
    assert blocks[0]["display_name"] == "智能体元数据"
    assert len(blocks[0]["tables"]) >= 4
    first_table = blocks[0]["tables"][0]
    assert first_table["term"] == "智能体访问日志"
    assert first_table["desc"] == "记录 API 访问"


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_groups_from_dataset_menu():
    groups = DataQueryPrompts.build_dataset_navigation_groups(SAMPLE_MENU)
    assert len(groups) == 2
    first_group = groups[0]
    assert first_group["id"]
    assert "智能体" in first_group["title"]
    assert first_group["summary"]
    assert first_group["tags"]
    assert len(first_group["questions"]) >= 3
    assert first_group["questions"][0]["query"]
    assert first_group["followups"]
    assert first_group["related_data"][0]["dataset"] == "ai_agent_meta"
    assert "智能体访问日志" in first_group["related_data"][0]["tables"]


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_uses_business_scene_cards():
    markdown = DataQueryPrompts.build_dataset_navigation_fallback(SAMPLE_MENU)
    assert "### 📚 我的数据门户" in markdown
    assert "#### 智能体运行分析" in markdown
    assert "> 您当前可访问 **2** 个数据集" in markdown
    assert "**你可以这样问：**" in markdown
    assert "**相关数据：** 智能体元数据 (ai_agent_meta)" in markdown
    assert "**继续追问：**" in markdown
    assert "智能体访问日志" in markdown
    assert "记录 API 访问" in markdown
    assert "(quick:/dataset_menu)" in markdown


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_empty():
    markdown = DataQueryPrompts.build_dataset_navigation_fallback(
        "Available Datasets\n  (No authorized datasets available)"
    )
    assert "暂无可查询的数据集" in markdown
    assert "(quick:/dataset_menu)" in markdown


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_shows_raw_menu():
    markdown = DataQueryPrompts.build_dataset_navigation_fallback("plain text without dataset blocks")
    assert "plain text" in markdown
    assert "(quick:/dataset_menu)" in markdown


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_generate_navigation_markdown_uses_llm():
    llm_output = (
        "### 📚 我的数据门户\n---\n"
        "您可查询运维与销售数据。\n\n"
        "#### 运维监控\n"
        "- [🙋 查机房告警](quick:统计最近一周机房告警记录)\n\n"
        "### 💬 您可能还想了解\n---\n"
        "- [🙋 重新查看数据门户](quick:/dataset_menu)\n"
    )
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        markdown = await DatasetNavigationService._generate_navigation_markdown(SAMPLE_MENU)

    assert "我的数据门户" in markdown
    assert "(quick:统计最近一周机房告警记录)" in markdown
    mock_client.generate_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_generate_navigation_markdown_falls_back_when_llm_invalid():
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value="没有 quick 按钮的回复")

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        markdown = await DatasetNavigationService._generate_navigation_markdown(SAMPLE_MENU)

    assert "ai_agent_meta" in markdown
    assert "(quick:/dataset_menu)" in markdown


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_uses_dataset_menu_and_cache():
    llm_output = (
        "### 📚 我的数据门户\n---\n"
        "- [🙋 查告警](quick:统计最近一周机房告警记录)\n\n"
        "### 💬 您可能还想了解\n---\n"
        "- [🙋 重新查看数据门户](quick:/dataset_menu)\n"
    )
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)

    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=SAMPLE_MENU),
    ), patch.object(
        DatasetNavigationService,
        "_load_cached_navigation",
        AsyncMock(return_value=None),
    ) as load_cache, patch.object(
        DatasetNavigationService,
        "_save_cached_navigation",
        AsyncMock(),
    ) as save_cache, patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        payload = await DatasetNavigationService.build_navigation_for_user(
            AsyncMock(),
            user_id=7,
            is_admin=False,
        )

    assert payload["dataset_count"] == 2
    assert payload["dataset_menu_hash"]
    assert payload["generated_at"]
    assert payload["groups"]
    assert payload["groups"][0]["questions"]
    load_cache.assert_awaited_once()
    save_cache.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_refresh_skips_cached_markdown():
    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=SAMPLE_MENU),
    ), patch.object(
        DatasetNavigationService,
        "_load_cached_navigation",
        AsyncMock(return_value="cached"),
    ) as load_cache, patch.object(
        DatasetNavigationService,
        "_generate_navigation_markdown",
        AsyncMock(return_value="fresh"),
    ) as generate_markdown, patch.object(
        DatasetNavigationService,
        "_save_cached_navigation",
        AsyncMock(),
    ) as save_cache:
        payload = await DatasetNavigationService.build_navigation_for_user(
            AsyncMock(),
            user_id=7,
            is_admin=False,
            force_refresh=True,
        )

    assert payload["markdown"] == "fresh"
    load_cache.assert_not_awaited()
    generate_markdown.assert_awaited_once()
    save_cache.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_sorts_questions_by_redis_click_stats():
    click_stats = {
        "查询智能体访问日志最近100条明细记录": {
            "count": 5,
            "last_clicked_at": "2026-06-16T10:00:00+00:00",
        }
    }
    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=SAMPLE_MENU),
    ), patch.object(
        DatasetNavigationService,
        "_load_cached_navigation",
        AsyncMock(return_value="cached"),
    ), patch.object(
        DatasetNavigationService,
        "_load_question_click_stats",
        AsyncMock(return_value=click_stats),
    ):
        payload = await DatasetNavigationService.build_navigation_for_user(
            AsyncMock(),
            user_id=7,
            is_admin=False,
        )

    questions = payload["groups"][0]["questions"]
    assert questions[0]["query"] == "查询智能体访问日志最近100条明细记录"
    assert questions[0]["click_count"] == 5
    assert questions[0]["last_clicked_at"] == "2026-06-16T10:00:00+00:00"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_record_question_click_stores_redis_rank_and_metadata():
    redis = AsyncMock()
    with patch("app.services.dataset_navigation_service.get_redis", AsyncMock(return_value=redis)):
        await DatasetNavigationService.record_question_click(
            user_id=7,
            is_admin=False,
            dataset_menu_hash="abc123",
            query="查询智能体访问日志最近100条明细记录",
            label="查询明细",
            group_id="ai_agent_meta_智能体运行分析",
        )

    redis.zincrby.assert_awaited_once()
    redis.hset.assert_awaited_once()
    assert redis.expire.await_count == 2
