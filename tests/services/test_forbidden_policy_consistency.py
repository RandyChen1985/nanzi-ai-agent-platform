"""禁用策略的口径一致性回归（对抗性审查第二轮发现的问题）。

这一批的共同病根是**同一件事有两套以上规则**，而分歧方向恰好都让功能"静默失效"：

1. MCP 工具名算不算 shell：命令策略认 `run_command`/`terminal`，MCP 分支却只认
   `bash`/`shell`/`exec` —— 走 MCP 注册的同名工具因此完全绕过命令黑名单；
2. `enabled` 怎么判：运行时大小写敏感、候选清单大小写不敏感 —— 手写 `enabled: False`
   时运行时照样加载，只有清单把它剔除，于是"列不出来所以禁不掉"；
3. 什么算 MCP 绑定：运行时看冒号，候选清单看 `mcp-` 前缀 —— 服务名不带该前缀的自建 MCP
   被归进「内置工具」，错页签、丢描述，管理员根本找不到它。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


# ---------------------------------------------------------------------------
# 1. MCP 形态的 shell 工具不能绕过命令禁用
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tool_name",
    [
        "bash", "sh", "shell", "exec", "exec_command", "execute_command",
        "run_command", "command", "terminal",          # 直连形态
        "mcp_terminal_abc123", "mcp_run_command_abc",  # MCP 注册名（此前漏判）
        "mcp_exec_command_abc", "mcp_bash_abc", "mcp_shell_abc",
    ],
)
def test_shell_like_detection_covers_mcp_registered_names(tool_name):
    """直连与 MCP 两条路径必须得出同一结论，否则等于给命令禁用开了后门。"""
    from app.services.ai.runtime.command_policy import is_shell_like_tool_name

    assert is_shell_like_tool_name(tool_name) is True, (
        f"{tool_name} 应被判为 shell —— 漏判会让该工具携带的命令不过命令黑名单"
    )


@pytest.mark.parametrize("tool_name", ["mcp_jira_search_abc", "mcp_postgres_query_x", "write_file"])
def test_non_shell_tools_stay_out_of_command_matching(tool_name):
    """放宽 marker 不能把带 query 参数的普通 MCP 工具拉进命令匹配。"""
    from app.services.ai.runtime.command_policy import is_shell_like_tool_name

    assert is_shell_like_tool_name(tool_name) is False


def test_mcp_shell_markers_cover_shell_tool_names_semantics():
    """marker 必须覆盖 SHELL_TOOL_NAMES 的语义（否则又会出现"直连拦、MCP 放行"）。

    唯一的例外是 ``sh``：它作为子串会误伤 ``push`` / ``share`` / ``flush`` 这类工具，
    代价比收益大，因此**刻意**不做 marker——「MCP 工具恰好取名为 sh」是极窄的漏判面，
    而误判普通工具会把它们的 query 参数拉进命令匹配。这个取舍在代码注释里写明了。
    """
    from app.services.ai.runtime import command_policy as cp

    intentionally_excluded = {"sh"}
    for name in cp.SHELL_TOOL_NAMES - intentionally_excluded:
        registered = f"mcp_{name}_deadbeef"
        assert cp.is_shell_like_tool_name(registered) is True, (
            f"SHELL_TOOL_NAMES 里的 {name} 走 MCP 注册后必须同样被判为 shell"
        )

    # 反向确认「不误伤」这一侧真的成立
    for innocent in ("mcp_push_notification_x", "mcp_share_doc_y", "mcp_flush_cache_z"):
        assert cp.is_shell_like_tool_name(innocent) is False, (
            f"{innocent} 不含 shell 语义，不该被拉进命令匹配"
        )


# ---------------------------------------------------------------------------
# 2. enabled 判定必须只有一套
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("TRUE", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("FALSE", False), ("False", False), ("0", False), ("no", False), ("off", False),
        (True, True), (False, False),
    ],
)
def test_is_skill_enabled_is_case_insensitive(raw, expected):
    """大小写与布尔形态都要认：这是"三套口径"合并成一套后的核心保证。"""
    from app.utils.skill_metadata import is_skill_enabled

    assert is_skill_enabled({"enabled": raw}) is expected


def test_is_skill_enabled_defaults_when_absent():
    from app.utils.skill_metadata import is_skill_enabled

    assert is_skill_enabled({}) is True
    assert is_skill_enabled(None) is True
    assert is_skill_enabled({}, default=False) is False


def test_no_stale_enabled_comparisons_remain():
    """不许再出现就地比较的字面量口径（它们正是漂移的来源）。"""
    for rel in ("app/services/ai/skill_resolver.py", "app/services/agent_scoped_restrictions.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert 'enabled", "true"' not in source, f"{rel} 仍在就地比较 enabled 字面量"


@pytest.mark.parametrize(
    "case,expected_enabled",
    [("enabled: False", False), ("enabled: false", False), ("enabled: True", True)],
)
def test_hand_written_enabled_flag_agrees_end_to_end(case, expected_enabled):
    """手写 `enabled: False` 时，候选清单与运行时必须给出一致结论。

    此前运行时（敏感比较）认为它**仍启用**、清单（不敏感）认为它禁用，结果是技能被加载、
    却在弹框里看不到——管理员无法禁掉一个"正在生效"的技能。
    """
    from app.services import agent_scoped_restrictions as scope
    from app.utils.skill_metadata import is_skill_enabled

    value = case.split(":", 1)[1].strip()
    meta = {"id": "gamma", "name": "gamma", "enabled": value}
    assert is_skill_enabled(meta) is expected_enabled
    # 清单侧用的就是同一个函数，这里顺带确认它真的被引用
    source = (ROOT / "app/services/agent_scoped_restrictions.py").read_text(encoding="utf-8")
    assert "is_skill_enabled(meta)" in source


# ---------------------------------------------------------------------------
# 3. 什么算 MCP 绑定：与运行时同源（冒号）
# ---------------------------------------------------------------------------

def test_mcp_binding_is_recognized_by_colon_not_prefix():
    """服务名不含 `mcp-` 前缀时也必须归入 MCP，而不是被当成内置工具。"""
    from app.services.agent_scoped_restrictions import split_agent_tool_bindings

    plain, pairs = split_agent_tool_bindings(
        [
            "exec_command",
            "NanZi Echo 测试 MCP:get_current_date",   # 自建 MCP：无 mcp- 前缀
            "mcp-private-admin-1234:list_tickets",    # 历史写法：带前缀
        ]
    )

    assert plain == ["exec_command"], "不含冒号的才是内置/普通工具"
    assert pairs == [
        ("NanZi Echo 测试 MCP", "get_current_date"),
        ("private-admin-1234", "list_tickets"),
    ], "两种写法都要解析成 (server, tool)，且前缀只在存在时剥离"


def test_mcp_binding_agrees_with_runtime_namespace_rule():
    """候选清单的判据必须与运行时 `is_platform_namespaced_tool_name` 一致。"""
    from app.services.ai.tools.model_tool_name import is_platform_namespaced_tool_name

    assert is_platform_namespaced_tool_name("Some MCP:tool") is True
    assert is_platform_namespaced_tool_name("exec_command") is False


# ---------------------------------------------------------------------------
# 4. 字典形工具项不能被 str() 成 repr
# ---------------------------------------------------------------------------

def test_dict_tool_entries_keep_the_real_tool_name():
    """`{'name': ...}` 形态要取真名，否则该工具彻底不可限定、还多出垃圾候选。"""
    from app.services.agent_scoped_restrictions import _as_list

    entries = _as_list(
        [
            "exec_command",
            {"name": "get_dataset_schema", "metadata_dataset_ids": [1, 2]},
            {"tool_name": "send_dingtalk"},
            {"id": "lookup_user"},
            {"unknown": "no name field"},
        ]
    )

    assert entries == ["exec_command", "get_dataset_schema", "send_dingtalk", "lookup_user"]
    assert not any(e.startswith("{") for e in entries), "不允许出现 Python repr 形态的候选 id"


# ---------------------------------------------------------------------------
# 5. 全局禁用端点不接受非数组载荷
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["forbidden_tools", "forbidden_commands", "forbidden_skills"])
def test_permission_set_rejects_bare_string(field):
    """非数组载荷必须 422，而不是"按字符拆"后 200 成功并清空原规则。"""
    from app.schemas.permission import PermissionSet

    with pytest.raises(ValidationError):
        PermissionSet(**{field: "exec_command"})

    ok = PermissionSet(**{field: ["exec_command"]})
    assert getattr(ok, field) == ["exec_command"]


def test_permission_set_still_validates_length():
    from app.schemas.permission import PermissionSet

    with pytest.raises(ValidationError):
        PermissionSet(forbidden_tools=["x" * 256])


# ---------------------------------------------------------------------------
# 6. 提示清洗必须保留同段里的用户原文
# ---------------------------------------------------------------------------

def test_sanitize_keeps_user_text_that_shares_a_paragraph_with_the_hint():
    """只摘掉提到被禁技能路径的**行**，不能整段替换。

    分段按空行切，用户完全可能在同一段里追加自己的话。整段替换会把用户原文一起删掉：
    模型看不到原始问题、user_query 被换成禁令文案（影响路由与技能扫描），
    而改写结果会写进会话历史、不可恢复。
    """
    from app.utils.skill_metadata import sanitize_forbidden_skill_context

    message = {
        "role": "user",
        "content": (
            "帮我看看这个流程\n"
            "- 已挂载技能 gamma：/app/data/skills/gamma/SKILL.md\n"
            "另外把 /app/data/skills/gamma/SKILL.md 的内容读出来"
        ),
        "files": [],
    }
    sanitize_forbidden_skill_context([message], {"gamma"})

    assert "帮我看看这个流程" in message["content"], "用户自己的话必须保留"
    assert "/app/data/skills/gamma/SKILL.md" not in message["content"], (
        "被禁技能的绝对路径必须清理干净（它是「直接去读」的教唆）"
    )
    assert "gamma" in message["content"], "应留下明确的不可用说明"


def test_sanitize_leaves_unrelated_paragraphs_untouched():
    from app.utils.skill_metadata import sanitize_forbidden_skill_context

    message = {
        "role": "user",
        "content": "第一段完全无关\n\n第二段提到 /app/data/skills/gamma/SKILL.md",
        "files": [],
    }
    sanitize_forbidden_skill_context([message], {"gamma"})

    assert message["content"].startswith("第一段完全无关"), "无关段落不得被改动"
    assert "/app/data/skills/gamma/SKILL.md" not in message["content"]


# ---------------------------------------------------------------------------
# 7. 会话快照清理不得连坐个人技能
# ---------------------------------------------------------------------------

def test_snapshot_pruning_requires_matching_skill_id():
    """逐字节复制平台技能的个人技能不能被一起删（个人技能永不受限）。"""
    from app.services.ai.runtime.agentscope.workspace import _entry_is_forbidden

    assert _entry_is_forbidden({"hash": "h", "skill_id": "my-personal-copy"}, {"gamma"}) is False
    assert _entry_is_forbidden({"hash": "h", "skill_id": "gamma"}, {"gamma"}) is True
    # 旧索引没有 skill_id 字段：回退哈希判定，与改动前一致（不会漏清理）
    assert _entry_is_forbidden({"hash": "h"}, {"gamma"}) is True
    assert _entry_is_forbidden({"hash": "h", "skill_id": "gamma"}, set()) is False


def test_snapshot_index_records_skill_id():
    """播种时必须把来源 skill_id 写进索引，否则上面的复核没有依据。"""
    source = (ROOT / "app/services/ai/runtime/agentscope/workspace.py").read_text(encoding="utf-8")
    # 来源 id 统一由一个变量推出（同源改版替换也复用它），不再在写入处内联表达式
    assert "skill_id = os.path.basename(os.path.normpath(skill_path))" in source, (
        "必须从技能目录推出来源 id"
    )
    assert '"skill_id": skill_id' in source, "索引条目必须记录来源技能 id"
    # 账本读取统一收在 _read_session_skills_ledger 里（新位置 + 老位置 skills/.skills 回退）
    assert '"skill_id": str(entry.get("skill_id", ""))' in source, "读取索引时必须保留该字段"


# ---------------------------------------------------------------------------
# 8. model 白名单也必须限定作用域
# ---------------------------------------------------------------------------

def test_model_whitelist_query_is_agent_scope_filtered():
    """model 是白名单（空=不限制）：漏掉作用域会把各智能体的白名单并集成"处处可用"。"""
    source = (ROOT / "app/services/permission_service.py").read_text(encoding="utf-8")
    body = source[source.index("async def get_chat_allowed_model_ids"):]
    body = body[: body.index("async def update_user_permissions")]
    assert "_agent_scope_filters" in body, "model 白名单查询必须带上作用域过滤"


# ---------------------------------------------------------------------------
# 9. 首轮强制预载已下线
# ---------------------------------------------------------------------------

def test_no_first_turn_superpowers_forced_preload():
    """新会话首轮强制预载 using-superpowers 的口径已移除。

    该门禁曾对**所有智能体**生效、绕过 skills_custom 白名单，把 5.4 KB 的软件工程方法论
    塞进每个新会话首轮（额外写一条「已启用技能」时间线日志，并计入技能激活统计），对业务
    问答是纯噪音。移除后技能注入只剩「按本轮问题语义扫描 + 显式挂载」两条路，本用例防止
    它被悄悄加回来。
    """
    source = (ROOT / "app/services/ai/skills/injector.py").read_text(encoding="utf-8")

    assert "using-superpowers" not in source
    assert "append_first_turn_superpowers" not in source
    assert "ensure_first_turn_superpowers_candidate" not in source
    assert "is_new_session_first_user_turn" not in source


# ---------------------------------------------------------------------------
# 10. 候选清单缺视角参数时不能静默
# ---------------------------------------------------------------------------

def test_restrictable_resources_warns_without_perspective():
    """漏传 user_id/role_id 会让所有项看起来都可选，是一条日志里可见的 warning。"""
    source = (ROOT / "app/api/portal/endpoints/management.py").read_text(encoding="utf-8")
    body = source[source.index("async def get_agent_restrictable_resources"):]
    body = body[: body.index("async def get_user_agent_restrictions")]
    assert "logger.warning" in body and "without user_id/role_id" in body, (
        "缺视角参数时必须留下可诊断的信号"
    )


# ---------------------------------------------------------------------------
# 11. 内置工具的中文描述要覆盖智能体真实绑定的那批
# ---------------------------------------------------------------------------

def test_builtin_tool_descriptions_cover_bound_tools():
    """候选目录只收 9 个高危工具，但智能体绑定里还有一大批普通内置工具。

    不补描述时它们在弹框里只显示裸英文 id，管理员得靠猜该不该限定。
    这里钉住几个真实库里出现频次最高的（此前全部无描述）。
    """
    from app.services.ai.tools.forbidden_catalog import builtin_tool_description

    for name in (
        "execute_system_command",
        "manage_system_process",
        "read_local_file",
        "write_local_file",
        "execute_sql_query",
        "get_dataset_schema",
        "search_knowledge_base",
        "sub_agent_call",
        "word_document_write",
        "web_search_baidu",
    ):
        assert builtin_tool_description(name), f"{name} 缺中文描述，界面上会显示裸 id"


def test_builtin_tool_description_tolerates_unknown_and_empty():
    from app.services.ai.tools.forbidden_catalog import builtin_tool_description

    assert builtin_tool_description("no-such-tool") == ""
    assert builtin_tool_description(None) == ""
    assert builtin_tool_description("") == ""


def test_restrictable_catalog_merges_the_description_table():
    """候选构建必须真的用上这张表，否则补了描述也不显示。"""
    source = (ROOT / "app/services/agent_scoped_restrictions.py").read_text(encoding="utf-8")
    assert "builtin_tool_description" in source, "候选构建要用描述表兜底"
    assert "for name in builtin:" in source, "兜底必须覆盖全部绑定的内置工具"


# ---------------------------------------------------------------------------
# 12. 平台自己的系统命令工具也必须过命令黑名单
# ---------------------------------------------------------------------------

def test_platform_system_command_tool_counts_as_shell():
    """`execute_system_command` 名字里没有 bash/sh/exec，此前完全不在判定里。

    它恰恰是最直接的一条 shell 通道，漏判等于命令禁用对它无效。
    """
    from app.services.ai.runtime.command_policy import is_shell_like_tool_name

    assert is_shell_like_tool_name("execute_system_command") is True
    assert is_shell_like_tool_name("mcp_execute_system_command_ab12") is True


# ---------------------------------------------------------------------------
# 13. 写入端不得留下"运行时不读"的技能 id
# ---------------------------------------------------------------------------

def test_write_path_prunes_unreadable_skill_ids():
    """纯个人技能 id / 不存在的 id 落库只会造成"回显已禁、实际没限住"。"""
    source = (ROOT / "app/services/permission_service.py").read_text(encoding="utf-8")
    assert "_drop_unusable_skill_ids" in source
    body = source[source.index("def _drop_unusable_skill_ids"):]
    body = body[: body.index("async def update_agent_scoped_forbidden_ids")]
    # 两条保护：目录异常、公共清单为空 —— 都必须原样保留
    assert body.count("return items") >= 2, "读不到目录或清单为空时必须原样保留，不能误删有效项"
    assert 'if resource_type == "forbidden_skill"' in source, "只在技能类别上应用"
