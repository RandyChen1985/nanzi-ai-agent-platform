from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply
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
def test_dataset_navigation_generation_prompt_uses_dataset_portal_command():
    prompt = DataQueryPrompts.dataset_navigation_generation_prompt(SAMPLE_MENU)
    assert "ai_agent_meta" in prompt
    assert "业务场景卡片" in prompt
    assert "我的数据门户" in prompt
    assert "示例问题" in prompt
    assert "继续追问" in prompt
    assert "{dataset_menu}" in prompt
    assert "/dataset_portal" in prompt


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
def test_build_dataset_navigation_groups_one_card_per_dataset():
    menu = """Available Datasets:
- Dataset: ops_alerts
  Display Name: 机房告警
  Includes Tables: 机房告警表
  Table Details:
    - 机房告警表: 告警记录

- Dataset: ops_devices
  Display Name: 设备监控
  Includes Tables: 设备状态表
  Table Details:
    - 设备状态表: 设备运行状态
"""
    groups = DataQueryPrompts.build_dataset_navigation_groups(menu)
    assert len(groups) == 2
    assert groups[0]["title"] == "机房告警"
    assert groups[1]["title"] == "设备监控"
    assert len(groups[0]["related_data"]) == 1
    assert groups[0]["related_data"][0]["dataset"] == "ops_alerts"
    assert groups[1]["related_data"][0]["dataset"] == "ops_devices"
    assert groups[0]["tags"] == ["机房告警表"]
    assert groups[1]["tags"] == ["设备状态表"]


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_groups_prefers_metrics_as_tags():
    menu = """Available Datasets:
- Dataset: sales_kpi
  Display Name: 销售指标
  Metrics: 收入, 回款率
  Includes Tables: 销售订单表
"""
    groups = DataQueryPrompts.build_dataset_navigation_groups(menu)
    assert len(groups) == 1
    assert groups[0]["title"] == "销售指标"
    assert groups[0]["tags"] == ["收入", "回款率"]
    assert groups[0]["metrics"] == ["收入", "回款率"]


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_uses_one_card_per_dataset():
    menu = """Available Datasets:
- Dataset: ops_alerts
  Display Name: 机房告警
  Includes Tables: 机房告警表

- Dataset: ops_devices
  Display Name: 设备监控
  Includes Tables: 设备状态表
"""
    markdown = DataQueryPrompts.build_dataset_navigation_fallback(menu)
    assert markdown.count("#### 机房告警") == 1
    assert markdown.count("#### 设备监控") == 1
    assert "机房告警 (ops_alerts)" in markdown
    assert "设备监控 (ops_devices)" in markdown


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_groups_from_dataset_menu():
    groups = DataQueryPrompts.build_dataset_navigation_groups(SAMPLE_MENU)
    assert len(groups) == 2
    first_group = groups[0]
    assert first_group["id"]
    assert first_group["title"] == "智能体元数据"
    assert first_group["summary"]
    assert first_group["tags"]
    assert "智能体访问日志" in first_group["tags"]
    assert len(first_group["questions"]) >= 3
    assert first_group["questions"][0]["query"]
    assert first_group["followups"]
    assert first_group["related_data"][0]["dataset"] == "ai_agent_meta"
    assert "智能体访问日志" in first_group["related_data"][0]["tables"]


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_uses_business_scene_cards():
    markdown = DataQueryPrompts.build_dataset_navigation_fallback(SAMPLE_MENU)
    assert "### 📚 我的数据门户" in markdown
    assert "#### 智能体元数据" in markdown
    assert "> 您当前可访问 **2** 个数据集" in markdown
    assert "**你可以这样问：**" in markdown
    assert "**相关数据：** 智能体元数据 (ai_agent_meta)" in markdown
    assert "**继续追问：**" in markdown
    assert "智能体访问日志" in markdown
    assert "记录 API 访问" in markdown
    assert "(quick:/dataset_portal)" in markdown


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_empty():
    markdown = DataQueryPrompts.build_dataset_navigation_fallback(
        "Available Datasets\n  (No authorized datasets available)"
    )
    assert "暂无可查询的数据集" in markdown
    assert "(quick:/dataset_portal)" in markdown


@pytest.mark.no_infrastructure
def test_build_dataset_navigation_fallback_shows_raw_menu():
    markdown = DataQueryPrompts.build_dataset_navigation_fallback("plain text without dataset blocks")
    assert "plain text" in markdown
    assert "(quick:/dataset_portal)" in markdown


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_generate_navigation_markdown_uses_llm():
    llm_output = (
        "### 📚 我的数据门户\n---\n"
        "您可查询运维与销售数据。\n\n"
        "#### 运维监控\n"
        "- [🙋 查机房告警](quick:统计最近一周机房告警记录)\n\n"
        "### 💬 您可能还想了解\n---\n"
        "- [🙋 重新查看数据门户](quick:/dataset_portal)\n"
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
        markdown, llm_err = await DatasetNavigationService._generate_navigation_markdown(SAMPLE_MENU)

    assert llm_err is None
    assert "我的数据门户" in markdown
    assert "(quick:统计最近一周机房告警记录)" in markdown
    mock_client.generate_text.assert_awaited_once()
    messages = mock_client.generate_text.await_args.args[0]
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert messages[1].role == "user"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_generate_questions_via_llm_includes_user_message():
    llm_output = "- [🙋 测试问题](quick:统计最近7天的访问量)"
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        questions = await DatasetNavigationService._generate_questions_via_llm("system prompt")

    assert len(questions) == 1
    messages = mock_client.generate_text.await_args.args[0]
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert "推荐问题" in messages[1].content[0].text


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
        markdown, llm_err = await DatasetNavigationService._generate_navigation_markdown(SAMPLE_MENU)

    assert llm_err == "模型返回内容无效或未包含推荐问题"
    assert "ai_agent_meta" in markdown
    assert "(quick:/dataset_portal)" in markdown


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_uses_dataset_menu_and_cache():
    llm_output = (
        "### 📚 我的数据门户\n---\n"
        "- [🙋 查告警](quick:统计最近一周机房告警记录)\n\n"
        "### 💬 您可能还想了解\n---\n"
        "- [🙋 重新查看数据门户](quick:/dataset_portal)\n"
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
    assert payload["is_fallback"] is False
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
        AsyncMock(return_value=("fresh", None)),
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
        "查询智能体访问日志最近10条明细记录": {
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
    assert questions[0]["query"] == "查询智能体访问日志最近10条明细记录"
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
            query="查询智能体访问日志最近10条明细记录",
            label="查询明细",
            group_id="ai_agent_meta",
        )

    redis.zincrby.assert_awaited_once()
    redis.hset.assert_awaited_once()
    assert redis.expire.await_count == 2


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_clear_question_click_removes_redis_rank_and_metadata():
    redis = AsyncMock()
    redis.zrem = AsyncMock(return_value=1)
    redis.hdel = AsyncMock(return_value=1)
    with patch("app.services.dataset_navigation_service.get_redis", AsyncMock(return_value=redis)):
        cleared = await DatasetNavigationService.clear_question_click(
            user_id=7,
            is_admin=False,
            dataset_menu_hash="abc123",
            query="查询智能体访问日志最近10条明细记录",
        )

    assert cleared is True
    redis.zrem.assert_awaited_once()
    redis.hdel.assert_awaited_once()


@pytest.mark.no_infrastructure
def test_parse_groups_from_markdown_success():
    static_groups = [
        {
            "id": "ai_agent_meta",
            "title": "智能体元数据",
            "summary": "静态描述",
            "questions": [{"label": "静态问题", "query": "静态查询"}],
            "followups": [{"label": "静态追问", "query": "静态追问查询"}],
        }
    ]
    markdown = (
        "### 📚 我的数据门户\n"
        "---\n"
        "> 整体描述\n"
        "\n"
        "#### 智能体元数据\n"
        "> 这是动态生成的智能体分析摘要，帮助分析性能。\n"
        "\n"
        "**你可以这样问：**\n"
        "- [🙋 访问热度](quick:分析智能体访问次数最高的记录)\n"
        "- [🙋 调用失败率](quick:统计最近10天报错的智能体执行日志)\n"
        "\n"
        "**相关数据：**\n"
        "- 智能体访问日志\n"
        "\n"
        "**继续追问：**\n"
        "- [🙋 性能分析](quick:说明智能体响应时长的分布)\n"
        "- [🙋 重新查看数据门户](quick:/dataset_portal)\n"
    )

    parsed = DatasetNavigationService.parse_groups_from_markdown(markdown, static_groups)
    assert len(parsed) == 1
    group = parsed[0]
    assert group["title"] == "智能体元数据"
    assert group["summary"] == "这是动态生成的智能体分析摘要，帮助分析性能。"
    assert len(group["questions"]) == 2
    assert group["questions"][0]["label"] == "访问热度"
    assert group["questions"][0]["query"] == "分析智能体访问次数最高的记录"
    assert group["questions"][0]["type"] == "dynamic"
    assert len(group["followups"]) == 1
    assert group["followups"][0]["label"] == "性能分析"
    assert group["followups"][0]["query"] == "说明智能体响应时长的分布"


@pytest.mark.no_infrastructure
def test_parse_groups_from_markdown_fallback_on_invalid():
    static_groups = [
        {
            "id": "test",
            "title": "测试场景",
            "summary": "静态描述",
            "questions": [{"label": "静态问题", "query": "静态"}],
        }
    ]
    # 无匹配的#### 场景块
    parsed = DatasetNavigationService.parse_groups_from_markdown("### 📚 只有标题", static_groups)
    assert parsed == static_groups


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_uses_short_ttl_on_fallback():
    fallback_markdown = DataQueryPrompts.build_dataset_navigation_fallback(SAMPLE_MENU)
    
    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=SAMPLE_MENU),
    ), patch.object(
        DatasetNavigationService,
        "_load_cached_navigation",
        AsyncMock(return_value=None),
    ), patch.object(
        DatasetNavigationService,
        "_generate_navigation_markdown",
        AsyncMock(return_value=(fallback_markdown, "模型调用失败")),
    ), patch.object(
        DatasetNavigationService,
        "_save_cached_navigation",
        AsyncMock(),
    ) as save_cache:
        payload = await DatasetNavigationService.build_navigation_for_user(
            AsyncMock(),
            user_id=7,
            is_admin=False,
        )
    
    assert payload["is_fallback"] is True
    assert payload["llm_generation_failed"] is True
    assert payload["llm_error_message"] == "模型调用失败"
    save_cache.assert_awaited_once_with(ANY, fallback_markdown, ttl=15)


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_generate_navigation_markdown_returns_error_when_llm_raises():
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(side_effect=RuntimeError("Qwen 400"))

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        markdown, llm_err = await DatasetNavigationService._generate_navigation_markdown(SAMPLE_MENU)

    assert llm_err == "Qwen 400"
    assert "ai_agent_meta" in markdown


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_sets_llm_generation_failed():
    fallback_markdown = DataQueryPrompts.build_dataset_navigation_fallback(SAMPLE_MENU)
    fallback_md = finalize_visible_reply(fallback_markdown, collapse_duplicates=False)

    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=SAMPLE_MENU),
    ), patch.object(
        DatasetNavigationService,
        "_load_cached_navigation",
        AsyncMock(return_value=None),
    ), patch.object(
        DatasetNavigationService,
        "_generate_navigation_markdown",
        AsyncMock(return_value=(fallback_md, "模型不可用")),
    ), patch.object(
        DatasetNavigationService,
        "_save_cached_navigation",
        AsyncMock(),
    ):
        payload = await DatasetNavigationService.build_navigation_for_user(
            AsyncMock(),
            user_id=7,
            is_admin=False,
        )

    assert payload["is_fallback"] is True
    assert payload["llm_generation_failed"] is True
    assert payload["llm_error_message"] == "模型不可用"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_bump_navigation_cache_generation_increments_redis_counter():
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=2)
    with patch("app.services.dataset_navigation_service.get_redis", AsyncMock(return_value=redis)):
        await DatasetNavigationService.bump_navigation_cache_generation()
    redis.incr.assert_awaited_once()


@pytest.mark.no_infrastructure
def test_build_group_followups_refresh_prompt_contains_scene_and_tables():
    prompt = DataQueryPrompts.build_group_followups_refresh_prompt(
        group_title="智能体元数据",
        tables=["智能体访问日志"],
        table_to_columns={
            "智能体访问日志": [
                {"name": "request_count", "term": "访问量", "type": "int", "description": "总访问量"}
            ]
        },
        table_physical_names={"智能体访问日志": "ai_agent_execution_history"},
    )
    assert "智能体元数据" in prompt
    assert "智能体访问日志" in prompt
    assert "物理表名：ai_agent_execution_history" in prompt
    assert "不要在 quick 中输出物理表名" in prompt
    assert "2 条" in prompt or "2 行" in prompt


@pytest.mark.no_infrastructure
def test_build_group_questions_refresh_prompt_contains_excluded_questions():
    prompt = DataQueryPrompts.build_group_questions_refresh_prompt(
        group_title="智能体元数据",
        tables=["智能体访问日志"],
        table_to_columns={
            "智能体访问日志": [
                {"name": "request_count", "term": "访问量", "type": "int", "description": "总访问量"}
            ]
        },
        table_physical_names={"智能体访问日志": "ai_agent_execution_history"},
        exclude_questions=[
            "分析最近一周的智能体访问量",
            "统计最近7天每日访问量趋势",
        ],
    )
    assert "最近已经出现过" in prompt
    assert "分析最近一周的智能体访问量" in prompt
    assert "统计最近7天每日访问量趋势" in prompt


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_refresh_group_followups_success():
    llm_output = (
        "- [🙋 字段口径](quick:说明智能体访问日志有哪些关键指标口径)\n"
        "- [🙋 关联分析](quick:智能体访问日志还能和哪些表做关联分析)"
    )
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)

    mock_db = AsyncMock()
    mock_res_physical = MagicMock()
    mock_res_physical.all = MagicMock(return_value=[
        MagicMock(term="智能体访问日志", physical_name="ai_agent_execution_history")
    ])
    mock_res_columns = MagicMock()
    mock_res_columns.all = MagicMock(return_value=[])
    mock_db.execute = AsyncMock(side_effect=[mock_res_physical, mock_res_columns])

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        followups = await DatasetNavigationService.refresh_group_followups(
            mock_db,
            group_title="智能体元数据",
            tables=["智能体访问日志"],
        )

    assert len(followups) == 2
    assert followups[0]["label"] == "字段口径"
    assert "智能体访问日志" in followups[0]["query"]
    assert "物理表名" not in followups[0]["query"]


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_refresh_group_questions_success():
    llm_output = (
        "- [🙋 访问量统计](quick:分析最近一周的智能体访问量)\n"
        "- [🙋 耗时分析](quick:说明响应最长的前10个请求)\n"
        "- [🙋 失败原因分布](quick:统计最近24小时的失败原因分布)"
    )
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)

    mock_db = AsyncMock()
    mock_res_physical = MagicMock()
    mock_res_physical.all = MagicMock(return_value=[
        MagicMock(term="智能体访问日志", physical_name="ai_agent_execution_history")
    ])
    mock_res_columns = MagicMock()
    mock_res_columns.all = MagicMock(return_value=[
        MagicMock(table_term="智能体访问日志", physical_name="request_count", column_term="访问量", type="int", description="总访问量")
    ])
    mock_db.execute = AsyncMock(side_effect=[mock_res_physical, mock_res_columns])

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        questions = await DatasetNavigationService.refresh_group_questions(
            mock_db,
            group_title="智能体元数据",
            tables=["智能体访问日志"],
        )

    assert len(questions) == 3
    assert questions[0]["label"] == "访问量统计"
    assert questions[0]["query"] == "分析最近一周的智能体访问量"
    assert questions[0]["type"] == "dynamic"


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_refresh_group_questions_filters_to_authorized_tables_and_excludes_recent_questions():
    llm_output = (
        "- [🙋 访问量统计](quick:分析最近一周的智能体访问量)\n"
        "- [🙋 耗时分析](quick:说明响应最长的前10个请求)\n"
        "- [🙋 失败原因分布](quick:统计最近24小时的失败原因分布)"
    )
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)
    mock_db = AsyncMock()

    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=SAMPLE_MENU),
    ), patch.object(
        DatasetNavigationService,
        "_load_table_metadata",
        AsyncMock(return_value=(
            {"智能体访问日志": "ai_agent_execution_history"},
            {"智能体访问日志": [
                {"name": "request_count", "term": "访问量", "type": "int", "description": "总访问量"}
            ]},
        )),
    ) as load_metadata, patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ), patch(
        "app.services.dataset_navigation_service.get_redis",
        AsyncMock(return_value=None),
    ):
        questions = await DatasetNavigationService.refresh_group_questions(
            mock_db,
            group_title="智能体元数据",
            tables=["智能体访问日志", "未授权表"],
            user_id=7,
            is_admin=False,
            dataset_menu_hash="abc123",
            group_id="ai-agent-meta",
            exclude_questions=[
                {"label": "当前问题", "query": "分析最近一周的智能体访问量"},
            ],
        )

    load_metadata.assert_awaited_once_with(mock_db, ["智能体访问日志"])
    assert [q["query"] for q in questions] == [
        "说明响应最长的前10个请求",
        "统计最近24小时的失败原因分布",
    ]


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_recent_refresh_questions_are_stored_with_short_ttl():
    class FakeRedis:
        def __init__(self):
            self.values = {}
            self.ttls = {}

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, ex=None):
            self.values[key] = value
            self.ttls[key] = ex

    redis = FakeRedis()
    with patch(
        "app.services.dataset_navigation_service.get_redis",
        AsyncMock(return_value=redis),
    ):
        await DatasetNavigationService._remember_recent_refresh_questions(
            user_key="7",
            dataset_menu_hash="abc123",
            purpose="questions",
            group_identity="ai-agent-meta",
            questions=[
                {"label": "访问量统计", "query": "分析最近一周的智能体访问量"},
            ],
        )
        recent = await DatasetNavigationService._load_recent_refresh_questions(
            user_key="7",
            dataset_menu_hash="abc123",
            purpose="questions",
            group_identity="ai-agent-meta",
        )

    assert recent == ["分析最近一周的智能体访问量"]
    assert list(redis.ttls.values()) == [300]


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_recommend_table_questions_uses_frontend_columns_without_db_lookup():
    llm_output = (
        "- [🙋 用户增长趋势](quick:统计最近30天每日新增用户数)\n"
        "- [🙋 角色分布](quick:按角色统计当前用户数量分布)\n"
        "- [🙋 最近登录](quick:查询最近7天登录过的用户数量)"
    )
    mock_client = MagicMock()
    mock_client.generate_text = AsyncMock(return_value=llm_output)
    mock_db = AsyncMock()

    with patch(
        "app.services.dataset_navigation_service.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=object()),
    ), patch(
        "app.services.dataset_navigation_service.chat_client_from_handle",
        return_value=mock_client,
    ):
        questions = await DatasetNavigationService.recommend_table_questions(
            mock_db,
            table="智能体用户表",
            physical_table_name="ai_agent_users",
            dataset_name="智能体数据集",
            columns=[
                {
                    "name": "id",
                    "term": "用户ID",
                    "type": "bigint",
                    "description": "主键",
                }
            ],
        )

    assert len(questions) == 3
    assert questions[0]["label"] == "用户增长趋势"
    assert questions[0]["query"] == "统计最近30天每日新增用户数"
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_build_navigation_for_user_skips_llm_when_no_authorized_datasets():
    empty_menu = "Available Datasets\n  (No authorized datasets available)"

    with patch.object(
        DatasetNavigationService,
        "_get_dataset_menu",
        AsyncMock(return_value=empty_menu),
    ), patch.object(
        DatasetNavigationService,
        "_load_cached_navigation",
        AsyncMock(return_value=None),
    ) as load_cache, patch.object(
        DatasetNavigationService,
        "_generate_navigation_markdown",
        AsyncMock(),
    ) as generate_markdown, patch.object(
        DatasetNavigationService,
        "_save_cached_navigation",
        AsyncMock(),
    ) as save_cache:
        payload = await DatasetNavigationService.build_navigation_for_user(
            AsyncMock(),
            user_id=7,
            is_admin=False,
        )

    assert payload["dataset_count"] == 0
    assert payload["has_datasets"] is False
    assert payload["is_fallback"] is False
    assert payload["groups"] == []
    assert payload["from_cache"] is False
    assert "开通" in payload["markdown"] or "权限" in payload["markdown"]
    load_cache.assert_not_awaited()
    generate_markdown.assert_not_awaited()
    save_cache.assert_not_awaited()
