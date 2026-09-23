import pytest

from app.services.ai.runtime.agentscope.process_timeline_snapshot import (
    apply_stream_chunk,
    finalize_process_timeline,
)


pytestmark = pytest.mark.no_infrastructure


def _run(chunks):
    state = []
    for chunk in chunks:
        apply_stream_chunk(state, chunk)
    return finalize_process_timeline(state)


def test_committed_narration_and_tool_are_kept_promoted_candidate_is_dropped():
    items = _run(
        [
            {"type": "process_narration", "content": "我先搜一下。"},
            {"type": "process_narration_commit", "content": "我先搜一下。"},
            {
                "type": "log",
                "id": "tool_1",
                "title": "调用工具: search",
                "details": "query=晋景",
                "status": "success",
                "category": "tool",
                "execution_time_ms": 120,
            },
            {"type": "process_narration", "content": "# 最终报告\n正文"},
            {"type": "process_narration_promote", "content": "# 最终报告\n正文"},
        ]
    )

    kinds = [(item.get("kind"), item.get("textKind"), item.get("pending")) for item in items]
    assert ("text", "narration", False) in kinds
    assert not any(item.get("pending") for item in items)
    narration = next(item for item in items if item.get("textKind") == "narration")
    assert narration["content"] == "我先搜一下。"
    assert narration["children"][0]["title"] == "调用工具: search"
    assert "# 最终报告" not in str(items)


def test_interrupted_narration_is_kept_as_finalized_history_item():
    items = _run([{"type": "process_narration", "content": "正在查询天气"}])

    assert items == [{
        "kind": "text",
        "id": "narration_1",
        "textKind": "narration",
        "content": "正在查询天气",
        "pending": False,
        "interrupted": True,
        "children": [],
    }]


def test_router_log_becomes_intent_style_step_without_raw_event_fields():
    items = _run(
        [
            {
                "type": "router_log",
                "thought": "用户在问数据",
                "selected_agent": "chatbi",
                "confidence": 0.9,
                "status": "success",
                "execution_time_ms": 40,
            }
        ]
    )

    assert len(items) == 1
    assert items[0]["kind"] == "log"
    assert items[0]["id"] == "route:target_selection"
    assert items[0]["title"] == "智能路由决策"
    assert items[0]["category"] == "router"
    assert "用户在问数据" in items[0]["details"]
    assert "chatbi" in items[0]["details"]
    assert "thought" not in items[0]


def test_router_log_updates_route_selection_without_creating_duplicate_step():
    items = _run(
        [
            {
                "type": "log",
                "id": "route:target_selection",
                "title": "判断并匹配目标专家",
                "status": "success",
                "category": "router",
                "execution_time_ms": 6600,
            },
            {
                "type": "router_log",
                "thought": "内部路由原因",
                "selected_agent": "chatbi",
                "confidence": 0.9,
                "status": "success",
                "execution_time_ms": 7200,
            },
        ]
    )

    assert [item["id"] for item in items] == ["route:target_selection"]
    assert items[0]["title"] == "判断并匹配目标专家"
    assert items[0]["execution_time_ms"] == 6600


def test_tool_details_are_truncated_and_empty_snapshot_is_omitted():
    huge = "抓取结果" * 800
    items = _run(
        [
            {
                "type": "log",
                "id": "tool_big",
                "title": "调用工具: crawl",
                "details": huge,
                "status": "success",
                "category": "tool",
            }
        ]
    )
    assert len(items[0]["details"]) < len(huge)
    assert items[0]["details"].endswith("…")
    assert finalize_process_timeline([]) is None
    interrupted = finalize_process_timeline([{"kind": "text", "id": "n", "textKind": "narration", "pending": True, "content": "候选"}])
    assert interrupted[0]["interrupted"] is True


def test_file_metadata_survives_process_timeline_persistence():
    metadata = {
        "operation": "read",
        "path": "/workspace/docs/report.md",
        "target_type": "file",
    }
    items = _run([
        {
            "type": "log",
            "id": "tool_file",
            "title": "工具完成: Read",
            "details": "正文",
            "status": "success",
            "category": "tool",
            "file_metadata": metadata,
        }
    ])

    assert items[0]["file_metadata"] == metadata


def test_model_call_start_and_end_merge_into_one_timeline_step():
    items = _run(
        [
            {
                "type": "model_call",
                "phase": "start",
                "reply_id": "r1",
                "model_name": "deepseek-chat",
            },
            {"type": "process_narration", "content": "我先搜一下。"},
            {"type": "process_narration_commit", "content": "我先搜一下。"},
            {
                "type": "log",
                "id": "tool_1",
                "title": "调用工具: search",
                "status": "success",
                "category": "tool",
            },
            {
                "type": "model_call",
                "phase": "end",
                "reply_id": "r1",
                "input_tokens": 100,
                "output_tokens": 20,
                "duration_ms": 1500,
            },
            {
                "type": "model_call",
                "phase": "start",
                "reply_id": "r2",
                "model_name": "deepseek-chat",
            },
            {
                "type": "model_call",
                "phase": "end",
                "reply_id": "r2",
                "input_tokens": 80,
                "output_tokens": 40,
                "duration_ms": 900,
            },
        ]
    )

    models = [item for item in items if item.get("category") == "model"]
    assert len(models) == 2
    assert models[0]["title"] == "模型调用: deepseek-chat"
    assert models[0]["status"] == "success"
    assert models[0]["details"] == "输入 100 / 输出 20 tokens，耗时 1500 ms"
    assert models[0]["execution_time_ms"] == 1500
    assert models[1]["details"] == "输入 80 / 输出 40 tokens，耗时 900 ms"
    assert items[0]["category"] == "model"
    assert items[1]["textKind"] == "narration"


def test_todo_update_keeps_only_the_latest_complete_checklist():
    items = _run(
        [
            {
                "type": "todo_update",
                "todos": [
                    {"content": "检索知识库", "status": "in_progress"},
                    {"content": "整理答案", "status": "pending"},
                ],
                "counts": {"pending": 1, "in_progress": 1, "completed": 0},
            },
            {
                "type": "todo_update",
                "todos": [
                    {"content": "检索知识库", "status": "completed"},
                    {"content": "整理答案", "status": "in_progress"},
                ],
                "counts": {"pending": 0, "in_progress": 1, "completed": 1},
            },
        ]
    )

    todo_items = [item for item in items if item.get("kind") == "todo"]
    assert len(todo_items) == 1
    assert todo_items[0]["todos"][0]["status"] == "completed"
    assert todo_items[0]["todos"][1]["status"] == "in_progress"
    assert todo_items[0]["counts"] == {"pending": 0, "in_progress": 1, "completed": 1}


def test_empty_todo_update_removes_the_current_checklist():
    items = _run(
        [
            {
                "type": "todo_update",
                "todos": [{"content": "检索知识库", "status": "in_progress"}],
                "counts": {"pending": 0, "in_progress": 1, "completed": 0},
            },
            {"type": "todo_update", "todos": [], "counts": {"pending": 0, "in_progress": 0, "completed": 0}},
        ]
    )

    assert items is None


def test_malformed_todo_update_does_not_corrupt_other_timeline_items():
    items = _run(
        [
            {
                "type": "log",
                "id": "tool_1",
                "title": "调用工具: search_knowledge_base",
                "details": "ok",
                "status": "success",
                "category": "tool",
            },
            {"type": "todo_update", "todos": [{"content": "缺少状态"}]},
        ]
    )

    assert len(items) == 1
    assert items[0]["kind"] == "log"
    assert items[0]["title"] == "调用工具: search_knowledge_base"


def test_history_persistence_contract_covers_redis_audit_and_api():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    mysql = (root / "db-prod/V121-add_process_timeline_to_history.sql").read_text(encoding="utf-8")
    pg = (root / "db-prod-pg/V21-add_process_timeline_to_history.sql").read_text(encoding="utf-8")
    model = (root / "app/models/audit.py").read_text(encoding="utf-8")
    schema = (root / "app/schemas/agent.py").read_text(encoding="utf-8")
    audit = (root / "app/services/ai/audit.py").read_text(encoding="utf-8")
    chat = (root / "app/api/v1/endpoints/chat.py").read_text(encoding="utf-8")
    memory = (root / "app/services/ai/memory_service.py").read_text(encoding="utf-8")

    assert "process_timeline" in mysql
    assert "process_timeline" in pg
    assert "process_timeline" in model
    assert "process_timeline" in schema
    assert "process_timeline" in audit
    assert '"process_timeline"' in chat or "process_timeline" in chat
    assert "process_timeline" in memory


def test_user_question_card_is_persisted_with_options_for_history_replay():
    """提问卡必须随 process_timeline 定稿落库。

    实时卡片挂在消息对象的 userQuestion 上且不落库，历史会话只能依赖这里；
    且 finalize 阶段的白名单压缩不能把卡片快照洗掉。
    """
    items = _run(
        [
            {
                "type": "user_question",
                "question_id": "uq_abc123",
                "question": "请选择要分析的数据集",
                "options": [
                    {"id": "ds_1", "label": "销售数据集", "description": "近 30 天"},
                    {"id": "ds_2", "label": "库存数据集"},
                ],
                "is_multi_select": False,
                "allow_custom_input": True,
                "context": "需要确定分析口径",
                "purpose": "chatbi_dataset_selection",
                "tool_call_id": "call_1",
            }
        ]
    )

    assert items is not None and len(items) == 1
    entry = items[0]
    assert entry["kind"] == "log"
    assert entry["category"] == "user_question"
    assert entry["id"] == "user_question_uq_abc123"
    assert entry["details"] == "请选择要分析的数据集"
    assert entry["user_question"] == {
        "question_id": "uq_abc123",
        "question": "请选择要分析的数据集",
        "options": [
            {"id": "ds_1", "label": "销售数据集", "description": "近 30 天"},
            {"id": "ds_2", "label": "库存数据集"},
        ],
        "is_multi_select": False,
        "allow_custom_input": True,
        "context": "需要确定分析口径",
        "purpose": "chatbi_dataset_selection",
        "tool_call_id": "call_1",
    }


def test_user_question_without_usable_options_degrades_to_plain_log():
    """结构不完整的卡片不落快照，退化为普通日志行，避免历史回放拿到残卡。"""
    items = _run(
        [
            {
                "type": "user_question",
                "question_id": "uq_bad",
                "question": "只有一个可选项",
                "options": [{"id": "only", "label": "唯一"}],
            }
        ]
    )

    assert items is not None and len(items) == 1
    assert items[0]["category"] == "user_question"
    assert "user_question" not in items[0]
    assert items[0]["details"] == "只有一个可选项"


def test_tool_args_survive_process_timeline_persistence():
    """命令（工具入参）必须随 process_timeline 定稿落库。

    实时卡片靠 SSE 的 tool_args 字段；历史回放只能依赖快照，
    且 finalize 的白名单压缩不能把命令洗掉，否则刷新后只剩工具输出。
    """
    items = _run(
        [
            {
                "type": "log",
                "id": "bash_1",
                "title": "调用工具: Bash",
                "details": "",
                "tool_args": "npm run build",
                "status": "pending",
                "category": "tool",
            },
            {
                "type": "log",
                "id": "bash_1",
                "title": "工具完成: Bash (1200ms)",
                "details": "build ok",
                "status": "success",
                "category": "tool",
            },
        ]
    )

    assert items is not None and len(items) == 1
    entry = items[0]
    assert entry["kind"] == "log"
    assert entry["tool_args"] == "npm run build"
    assert entry["details"] == "build ok"


def test_tool_args_are_truncated_on_persistence():
    items = _run(
        [
            {
                "type": "log",
                "id": "bash_2",
                "title": "工具完成: Bash (10ms)",
                "details": "ok",
                "tool_args": "echo " + "x" * 4000,
                "status": "success",
                "category": "tool",
            },
        ]
    )

    assert items is not None
    stored = items[0]["tool_args"]
    assert len(stored) < 4000
    assert stored.startswith("echo ")


def test_tool_call_metadata_survives_process_timeline_persistence():
    """模型、温度与框架侧结果状态同样要随快照落库，否则刷新后排查线索就没了。"""
    items = _run(
        [
            {
                "type": "log",
                "id": "bash_meta",
                "title": "工具完成: Bash (10ms)",
                "details": "ok",
                "status": "error",
                "category": "tool",
                "model": "DeepSeek-V3.2",
                "temperature": 0.2,
                "tool_result_state": "timeout",
            },
        ]
    )

    assert items is not None and len(items) == 1
    entry = items[0]
    assert entry["model"] == "DeepSeek-V3.2"
    assert entry["temperature"] == 0.2
    assert entry["tool_result_state"] == "timeout"
