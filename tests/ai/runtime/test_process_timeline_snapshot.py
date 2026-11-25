import pytest

from app.services.ai.runtime.agentscope.process_timeline_snapshot import (
    apply_stream_chunk,
    finalize_process_timeline,
    merge_process_timeline_sequence,
    merge_process_timelines,
    merge_reasoning_contents,
    merge_text_contents,
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


def test_auto_approved_survives_process_timeline_persistence():
    """「本会话已授权」是审计留痕，定稿白名单不能把它洗掉，否则刷新/回放就看不见。"""

    items = _run(
        [
            {
                "type": "log",
                "id": "bash_grant",
                "title": "工具完成: Bash (900ms)",
                "details": "ok",
                "status": "success",
                "category": "tool",
                "auto_approved": True,
            },
        ]
    )

    assert items is not None and len(items) == 1
    assert items[0]["auto_approved"] is True


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


def test_tool_summary_survives_process_timeline_persistence():
    """命令意图摘要要随快照落库，否则刷新或历史回放就只剩「工具完成 · Bash」。"""
    items = _run(
        [
            {
                "type": "log",
                "id": "bash_summary",
                "title": "调用工具: Bash",
                "details": "",
                "tool_args": "pytest tests/frontend -q",
                "tool_summary": "前端契约全量回归",
                "status": "pending",
                "category": "tool",
            },
            {
                "type": "log",
                "id": "bash_summary",
                "title": "工具完成: Bash",
                "details": "1227 passed",
                "status": "success",
                "category": "tool",
            },
        ]
    )

    assert items is not None and len(items) == 1
    assert items[0]["tool_summary"] == "前端契约全量回归"

    # 创建路径（单条完成事件）同样要落库
    direct = _run(
        [
            {
                "type": "log",
                "id": "bash_summary_2",
                "title": "工具完成: Bash",
                "details": "ok",
                "tool_summary": "查看容器状态",
                "status": "success",
                "category": "tool",
            },
        ]
    )

    assert direct is not None and len(direct) == 1
    assert direct[0]["tool_summary"] == "查看容器状态"


def test_subagent_reasoning_log_attached_to_container_children_and_preserves_reasoning_flag():
    """子代理思考日志不得误判为委派容器或顶层平级项，且定稿后必须保留 reasoning=True。"""
    items = _run(
        [
            {
                "type": "log",
                "id": "subagent_run-1",
                "title": "委派智能体 · 数据分析专家",
                "details": "开始分析季度报表",
                "status": "pending",
                "category": "agent",
                "subagent": {"run_id": "run-1", "target_agent_name": "数据专家"},
            },
            {
                "type": "log",
                "id": "tool_run1_1",
                "title": "调用工具: execute_sql_query",
                "details": "SELECT 1",
                "status": "success",
                "category": "tool",
                "subagent": {"run_id": "run-1"},
            },
            {
                "type": "log",
                "id": "subagent_reasoning:run-1:1",
                "title": "子代理思考",
                "details": "分析数据结构并规划查询步骤",
                "status": "success",
                "category": "reasoning",
                "reasoning": True,
                "subagent": {"run_id": "run-1"},
            },
            {
                "type": "log",
                "id": "tool_call_sub_agent_finish",
                "title": "工具完成: sub_agent_call",
                "details": "委派完成汇总结果",
                "status": "success",
                "category": "agent",
                "subagent": {"run_id": "run-1"},
            },
        ]
    )

    assert items is not None
    # 顶层只能有 1 个真正的委派容器，思考日志不得自立门户或与容器并列
    assert len(items) == 1
    container = items[0]
    assert container["id"] == "subagent_run-1"
    assert container["status"] == "success"
    assert container["details"] == "委派完成汇总结果"

    children = container.get("children") or []
    child_ids = [c.get("id") for c in children]
    assert "tool_run1_1" in child_ids
    assert "subagent_reasoning:run-1:1" in child_ids

    reasoning_node = next(c for c in children if c.get("id") == "subagent_reasoning:run-1:1")
    assert reasoning_node.get("reasoning") is True
    assert reasoning_node.get("title") == "子代理思考"


def test_tool_after_non_narration_step_does_not_attach_to_stale_narration():
    """与前端 attachableNarrationParent 口径一致：旁白之后若已出现其他顶层步骤（如权限确认），
    后续工具调用不得跨阶段吸回旧旁白 children，必须作为顶层步骤按真实顺序留在后面。
    """
    items = _run(
        [
            {"type": "process_narration", "content": "我先确认文档目录。"},
            {"type": "process_narration_commit", "content": "我先确认文档目录。"},
            {
                "type": "log",
                "id": "glob_1",
                "title": "工具完成: Glob",
                "status": "success",
                "category": "tool",
            },
            {
                "type": "permission_result",
                "permission_request_id": "perm_1",
                "status": "success",
            },
            {
                "type": "log",
                "id": "bash_after_confirm",
                "title": "工具完成: Bash",
                "status": "success",
                "category": "tool",
            },
        ]
    )

    assert items is not None
    top_ids = [item.get("id") for item in items]
    assert top_ids == ["narration_1", "permission_perm_1", "bash_after_confirm"]
    assert [c.get("id") for c in items[0]["children"]] == ["glob_1"]


def test_agent_lifecycle_log_does_not_attach_to_narration():
    """「Agent 回复开始 / 结束」是阶段标记，不得被吸附进上一条旁白的 children。"""
    items = _run(
        [
            {"type": "process_narration", "content": "先检查一下。"},
            {"type": "process_narration_commit", "content": "先检查一下。"},
            {
                "type": "log",
                "id": "agent_reply_1",
                "title": "Agent 回复开始",
                "status": "success",
                "category": "agent",
            },
        ]
    )

    assert items is not None
    assert [item.get("id") for item in items] == ["narration_1", "agent_reply_1"]
    assert items[0].get("children") == []


def test_merge_process_timelines_combines_pre_and_post_approval_steps():
    """审批前与审批后的快照合并：保留前序准备/工具、同 id 原位更新并保护 tool_args、
    孤儿子步骤按 parent_id 挂回原父节点、撞 id 的新旁白不覆盖旧旁白、todo 仅留最新。
    """
    pre_approval = _run(
        [
            {
                "type": "log",
                "id": "preparation:auth_context_capability",
                "title": "鉴权及上下文与能力准备",
                "status": "success",
                "category": "system",
            },
            {
                "type": "log",
                "id": "grep_1",
                "title": "工具完成: Grep",
                "details": "matched 2 files",
                "status": "success",
                "category": "tool",
            },
            {"type": "process_narration", "content": "我先确认目录再执行命令。"},
            {"type": "process_narration_commit", "content": "我先确认目录再执行命令。"},
            {
                "type": "log",
                "id": "bash_1",
                "title": "调用工具: Bash",
                "details": "",
                "tool_args": "sleep 5s && echo ok",
                "tool_summary": "延时 5 秒后输出 ok",
                "status": "pending",
                "category": "tool",
            },
            {
                "type": "todo_update",
                "todos": [
                    {"content": "搜索文档", "status": "completed"},
                    {"content": "执行命令", "status": "in_progress"},
                ],
            },
            {
                "type": "log",
                "id": "permission_req_1",
                "title": "工具调用需要确认",
                "details": "确认请求: req_1",
                "status": "pending",
                "category": "permission",
            },
        ]
    )

    post_approval = _run(
        [
            {
                "type": "permission_result",
                "permission_request_id": "req_1",
                "status": "success",
            },
            {
                "type": "log",
                "id": "workspace:sandbox:bash_1",
                "parent_id": "bash_1",
                "title": "沙箱工作区准备",
                "details": "沙箱工作区准备就绪（40ms）",
                "status": "success",
                "category": "system",
                "execution_time_ms": 40,
            },
            {
                "type": "log",
                "id": "bash_1",
                "title": "工具完成: Bash",
                "details": '{"ok": true, "stdout": "ok\\n"}',
                "status": "success",
                "category": "tool",
                "execution_time_ms": 5020,
            },
            {"type": "process_narration", "content": "命令已完成，我来整理结果。"},
            {"type": "process_narration_commit", "content": "命令已完成，我来整理结果。"},
            {
                "type": "log",
                "id": "summary_1",
                "title": "汇总工具结果",
                "status": "success",
                "category": "tool",
            },
            {
                "type": "todo_update",
                "todos": [
                    {"content": "搜索文档", "status": "completed"},
                    {"content": "执行命令", "status": "completed"},
                ],
                "settled": True,
                "settledStatus": "success",
            },
        ]
    )

    merged = merge_process_timelines(pre_approval, post_approval)
    assert merged is not None

    top_ids = [item.get("id") for item in merged]
    # 前序准备、Grep、旧旁白、todo、permission 均在原位，新旁白追加在后且未覆盖旧旁白
    assert top_ids[:4] == [
        "preparation:auth_context_capability",
        "grep_1",
        "narration_3",
        "todo_current",
    ]
    assert "permission_req_1" in top_ids
    assert len([item for item in merged if item.get("kind") == "todo"]) == 1
    todo_item = next(item for item in merged if item.get("kind") == "todo")
    assert todo_item.get("settled") is True
    assert todo_item["todos"][1]["status"] == "completed"

    # permission_req_1 已由 pending 翻为 success
    perm_item = next(item for item in merged if item.get("id") == "permission_req_1")
    assert perm_item["status"] == "success"
    assert perm_item["title"] == "已允许工具调用"

    # bash_1 位于旧旁白 children 下：原位更新为 success，保留 tool_args/tool_summary，且挂回沙箱子步骤
    old_narration = next(item for item in merged if item.get("id") == "narration_3")
    assert old_narration["content"] == "我先确认目录再执行命令。"
    bash_node = next(c for c in old_narration["children"] if c.get("id") == "bash_1")
    assert bash_node["status"] == "success"
    assert bash_node["tool_args"] == "sleep 5s && echo ok"
    assert bash_node["tool_summary"] == "延时 5 秒后输出 ok"
    assert bash_node["execution_time_ms"] == 5020
    assert '{"ok": true' in bash_node["details"]
    assert [sub.get("id") for sub in bash_node.get("children") or []] == ["workspace:sandbox:bash_1"]
    assert "parent_id" not in bash_node["children"][0]

    # 后阶段新旁白保留独立节点（含 summary_1 子节点）
    text_items = [item for item in merged if item.get("kind") == "text"]
    assert [t["content"] for t in text_items] == [
        "我先确认目录再执行命令。",
        "命令已完成，我来整理结果。",
    ]
    assert text_items[0]["id"] != text_items[1]["id"]
    assert [c.get("id") for c in text_items[1]["children"]] == ["summary_1"]


def test_merge_process_timeline_sequence_is_idempotent_across_multiple_approvals():
    """单轮连续两次审批时，归并 [阶段1, 阶段1+2已合并, 阶段3] 不得产生重复旁白或重复步骤。"""
    stage1 = _run(
        [
            {"type": "log", "id": "prep", "title": "鉴权及上下文与能力准备", "status": "success", "category": "system"},
            {"type": "process_narration", "content": "先执行第一步。"},
            {"type": "process_narration_commit", "content": "先执行第一步。"},
            {"type": "log", "id": "tool_1", "title": "调用工具: Bash", "tool_args": "cmd1", "status": "pending", "category": "tool"},
        ]
    )
    stage2_delta = _run(
        [
            {"type": "log", "id": "tool_1", "title": "工具完成: Bash", "details": "ok1", "status": "success", "category": "tool"},
            {"type": "process_narration", "content": "再执行第二步。"},
            {"type": "process_narration_commit", "content": "再执行第二步。"},
            {"type": "log", "id": "tool_2", "title": "调用工具: Bash", "tool_args": "cmd2", "status": "pending", "category": "tool"},
        ]
    )
    stage1_plus_2 = merge_process_timelines(stage1, stage2_delta)
    stage3_delta = _run(
        [
            {"type": "log", "id": "tool_2", "title": "工具完成: Bash", "details": "ok2", "status": "success", "category": "tool"},
            {"type": "log", "id": "summary", "title": "汇总工具结果", "status": "success", "category": "tool"},
        ]
    )

    final_merged = merge_process_timeline_sequence([stage1, stage1_plus_2, stage3_delta])
    assert final_merged is not None

    narrations = [item for item in final_merged if item.get("kind") == "text"]
    assert [n["content"] for n in narrations] == ["先执行第一步。", "再执行第二步。"]
    assert [c["id"] for c in narrations[0]["children"]] == ["tool_1"]
    assert narrations[0]["children"][0]["status"] == "success"
    assert narrations[0]["children"][0]["tool_args"] == "cmd1"
    assert [c["id"] for c in narrations[1]["children"]] == ["tool_2"]
    assert narrations[1]["children"][0]["status"] == "success"
    assert narrations[1]["children"][0]["tool_args"] == "cmd2"
    assert [item["id"] for item in final_merged if item.get("kind") == "log"] == ["prep", "summary"]


def test_merge_reasoning_contents_deduplicates_across_multiple_resumes():
    assert merge_reasoning_contents(["第一段思考", None]) == "第一段思考"
    assert merge_reasoning_contents(["第一段思考", "第二段思考"]) == "第一段思考\n\n第二段思考"
    assert (
        merge_reasoning_contents(["第一段思考", "第一段思考\n\n第二段思考", "第三段思考"])
        == "第一段思考\n\n第二段思考\n\n第三段思考"
    )
    assert merge_reasoning_contents([None, ""]) is None


def test_merge_text_contents_deduplicates_across_multiple_resumes():
    assert merge_text_contents(["前半段正文。", ""]) == "前半段正文。"
    assert merge_text_contents(["前半段正文。", "后半段正文。"]) == "前半段正文。\n\n后半段正文。"
    assert (
        merge_text_contents(["前半段正文。", "前半段正文。\n\n后半段正文。", "第三段正文。"])
        == "前半段正文。\n\n后半段正文。\n\n第三段正文。"
    )
    # 已带换行时不强行多拼空行
    assert merge_text_contents(["第一段\n", "第二段"]) == "第一段\n第二段"
    assert merge_text_contents(["", None]) == ""


def test_merge_process_timelines_reallocates_colliding_synthetic_log_id_without_splitting_real_ids():
    """兜底合成 id（log_1）在两段快照撞 id 且标题不同时重分配唯一 id；
    但真实业务 id（工具/权限卡）即便挂起前后标题不同，仍必须按原 id 原位合并。
    """
    pre = _run(
        [
            {"type": "log", "title": "前序无 ID 步骤", "details": "step-a", "status": "success"},
            {
                "type": "log",
                "id": "call_bash_1",
                "title": "调用工具: Bash",
                "tool_args": "echo 1",
                "status": "pending",
                "category": "tool",
            },
        ]
    )
    post = _run(
        [
            {"type": "log", "title": "恢复轮无 ID 步骤", "details": "step-b", "status": "success"},
            {
                "type": "log",
                "id": "call_bash_1",
                "title": "工具完成: Bash",
                "details": "1",
                "status": "success",
                "category": "tool",
            },
        ]
    )

    merged = merge_process_timelines(pre, post)
    assert merged is not None
    # 两条无 id 步骤均保留且 id 互不冲突；真实业务 id 合并为同一条
    assert [item["title"] for item in merged] == [
        "前序无 ID 步骤",
        "工具完成: Bash",
        "恢复轮无 ID 步骤",
    ]
    assert merged[0]["id"] == "log_1"
    assert merged[2]["id"] != "log_1"
    assert merged[1]["id"] == "call_bash_1"
    assert merged[1]["tool_args"] == "echo 1"
    assert merged[1]["status"] == "success"

